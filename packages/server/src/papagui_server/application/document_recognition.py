"""Bounded document evaluation outside write transactions, with per-customer coverage."""
from __future__ import annotations

from collections import defaultdict
from contextlib import nullcontext
import hashlib
import uuid
import unicodedata

from papagui_contracts import CatalogProjectRoot, SourcePath
from papagui_server.domain.customer_recognition import document_candidates
from papagui_server.domain.errors import ResourceNotFoundError
from papagui_server.domain.models import ServerSettings
from papagui_server.domain.source_paths import source_uri

PIPELINE_VERSION = "customer-recognition-3"


def all_customers(repository):
    result = []
    offset = 0
    while True:
        values = repository.list(limit=500, offset=offset)
        result.extend(values)
        if len(values) < 500:
            return result
        offset += len(values)


class DocumentRecognitionService:
    def __init__(self, unit_of_work, catalog, settings=None, documents_per_project=24):
        self._work = unit_of_work
        self._catalog = catalog
        self._settings = settings
        self._document_limit = documents_per_project

    def run(self, source_id, *, customer_id=None, cancelled=lambda: False, exhaustive=False):
        settings = self._settings.load() if self._settings else ServerSettings(
            priority_documents_per_project=self._document_limit
        )
        manager = self._catalog.snapshot() if hasattr(self._catalog, "snapshot") else nullcontext(self._catalog)
        with manager as catalog:
            return self._evaluate(catalog, source_id, customer_id, settings, cancelled, exhaustive)

    def _evaluate(self, catalog, source_id, customer_id, settings, cancelled, exhaustive):
        roots = [CatalogProjectRoot.from_dict(value) for value in catalog.list_project_roots(source_id=source_id)]
        by_customer = defaultdict(list)
        with self._work() as work:
            customers = all_customers(work.customers) if customer_id is None else [work.customers.get(customer_id)]
            if customers == [None]:
                raise ResourceNotFoundError("Kunde nicht gefunden.")
            for root in roots:
                owner = work.projects.customer_id_for_source(root.source)
                if owner is not None:
                    by_customer[owner].append(root)
            work.commit()
        run_id = uuid.uuid4().hex
        stats = {"suggestions": 0, "evaluated": 0, "customers": 0}
        for customer in customers:
            if cancelled():
                raise InterruptedError("Kundendatenprüfung abgebrochen.")
            owner = int(customer["id"])
            customer_roots = by_customer[owner]
            root_ids = [root.id for root in customer_roots]
            coverages = catalog.coverage(source_id=source_id, project_root_ids=root_ids) if (
                customer_roots and hasattr(catalog, "coverage")
            ) else []
            counts = {"files_total": sum(row.get("files_total", 0) for row in coverages),
                      "eligible": sum(row.get("files_eligible", 0) for row in coverages),
                      "extracted": sum(row.get("files_extracted", 0) for row in coverages),
                      "evaluated": 0, "open": 0, "conflicts": 0,
                      "pages_total": sum(row.get("pages_total", 0) for row in coverages),
                      "pages_processed": sum(row.get("pages_processed", 0) for row in coverages),
                      "pages_unknown_documents": sum(row.get("pages_unknown_documents", 0) for row in coverages)}
            problems = sum(sum(v for k, v in row.get("status_counts", {}).items() if k not in {"ok", "unsupported"})
                           for row in coverages)
            evaluated = set()
            changed = False
            enabled = settings.recognition_pipeline_enabled and settings.priority_documents_per_project > 0
            needed = {key for key in ("company", "email", "phone", "street", "postal_code", "city") if not customer.get(key)}
            if not customer.get("contacts"):
                needed.add("contact")
            found = set()
            if enabled and customer_roots:
                first_limit = min(max(settings.priority_documents_per_project, 1), 500)
                maximum = max(first_limit, settings.recognition_documents_per_project_max)
                if exhaustive:
                    maximum = max(maximum, max((row.get("files_total", 0) for row in coverages), default=0))
                offsets = range(0, maximum, first_limit)
                for offset in offsets:
                    if offset and not exhaustive and not needed.difference(found):
                        break
                    if cancelled():
                        raise InterruptedError("Kundendatenprüfung abgebrochen.")
                    if hasattr(catalog, "coverage"):
                        documents = catalog.document_evidence(
                            source_id=source_id, documents_per_project=min(first_limit, maximum-offset),
                            project_root_ids=root_ids, offset_per_project=offset,
                        )
                    elif offset == 0:
                        documents = [d for d in catalog.document_evidence(
                            source_id=source_id, documents_per_project=first_limit
                        ) if d["project_root_id"] in root_ids]
                    else:
                        break
                    if not documents:
                        break
                    for document in documents:
                        if cancelled():
                            raise InterruptedError("Kundendatenprüfung abgebrochen.")
                        source = SourcePath.from_dict(document["source"])
                        uri = source_uri(source)
                        content = str(document["content"])
                        candidates = document_candidates(
                            content, customer_name=str(customer["display_name"]),
                            customer_aliases=tuple(v for v in [customer.get("company", "")] if v),
                            own_identities=tuple(v.strip() for v in settings.recognition_own_names.split(",") if v.strip()),
                            blocks=document.get("blocks", ()),
                        )
                        document_hash = document.get("content_hash") or hashlib.sha256(content.encode()).hexdigest()
                        family_text = " ".join(unicodedata.normalize("NFKC", content).casefold().split())
                        family = hashlib.sha256(family_text.encode()).hexdigest() if family_text else ""
                        # Parsing can be slow, so check ownership and revision again
                        # before attaching evidence to a short write transaction.
                        with self._work() as work:
                            current = work.customers.get(owner)
                            root = next((r for r in customer_roots if r.id == document["project_root_id"]), None)
                            if current is None or current["revision"] != customer["revision"] or root is None or (
                                work.projects.customer_id_for_source(root.source) != owner
                            ):
                                changed = True
                                work.commit()
                                break
                            for candidate in candidates:
                                blocked = work.blocklist.matches(
                                    candidate.field_name, candidate.value, candidate.payload,
                                )
                                if candidate.quality == "strong" and not blocked:
                                    found.add(candidate.field_name)
                                    found.update(candidate.payload if candidate.suggestion_type == "address" else ())
                                stats["suggestions"] += int(work.suggestions.add(
                                    owner, kind=candidate.field_name, value=candidate.value,
                                    source_path=uri, excerpt=candidate.excerpt, fingerprint="",
                                    confidence=candidate.confidence, rule=candidate.rule,
                                    normalized_value=candidate.normalized_value, party_key=candidate.party_key,
                                    party_role=candidate.party_role, quality=candidate.quality,
                                    reasons=candidate.reasons, suggestion_type=candidate.suggestion_type,
                                    payload=candidate.payload, source_locator=candidate.source_locator,
                                    document_hash=str(document_hash), document_family=family,
                                    engine_version=PIPELINE_VERSION, run_id=run_id,
                                ))
                            work.commit()
                        evaluated.add(uri)
                    if changed:
                        break
            counts["evaluated"] = len(evaluated)
            stats["evaluated"] += len(evaluated)
            existing = None
            if hasattr(catalog, "source_paths"):
                existing = {source_uri(SourcePath.from_dict(s)) for s in catalog.source_paths(
                    source_id=source_id, project_root_ids=root_ids
                )} if root_ids else set()
            if enabled and hasattr(catalog, "source_states"):
                # A definitive empty/new unsupported source invalidates old claims;
                # a temporary parser failure is not evidence of absent content.
                evaluated.update(source_uri(SourcePath.from_dict(row["source"]))
                                 for row in catalog.source_states(source_id=source_id, project_root_ids=root_ids)
                                 if row["extraction_status"] in {"no_text", "unsupported"})
            with self._work() as work:
                current = work.customers.get(owner)
                if current is None:
                    work.commit()
                    continue
                if enabled and not changed:
                    work.suggestions.finalize_run(owner, run_id,
                        evaluated_sources=evaluated, existing_sources=existing)
                    work.suggestions.reconcile_customer(current)
                counts["open"] = work.suggestions.count_for_customer(owner, status="pending")
                # Bound summaries independently of total candidate count.
                counts["conflicts"] = sum(s["is_conflict"] for s in work.suggestions.list_for_customer(
                    owner, status="pending", limit=500
                ))
                incomplete = changed or problems > 0 or counts["evaluated"] < counts["extracted"]
                if not enabled:
                    state, reason = "disabled", "disabled"
                elif not customer_roots:
                    state, reason = "complete", "no_projects"
                elif coverages and not counts["files_total"]:
                    state, reason = "complete", "no_documents"
                elif coverages and not counts["eligible"]:
                    state, reason = "partial", "unsupported"
                elif coverages and not counts["extracted"]:
                    state, reason = "partial", "no_text"
                elif incomplete:
                    state, reason = "partial", "partial"
                else:
                    state, reason = "complete", "review" if counts["open"] else "no_candidates"
                counts["changed_during_run"] = int(changed)
                counts["extraction_problems"] = problems
                counts["reasons"] = dict(sum_reason_counts(coverages))
                work.suggestions.set_recognition_status(owner, state=state, reason=reason, counts=counts,
                    pipeline_version=PIPELINE_VERSION,
                    catalog_version=catalog.snapshot_version() if hasattr(catalog, "snapshot_version") else "")
                work.commit()
            stats["customers"] += 1
        return stats


def sum_reason_counts(coverages):
    result = defaultdict(int)
    for coverage in coverages:
        for reason, count in coverage.get("reason_counts", {}).items():
            result[reason] += count
    return result
