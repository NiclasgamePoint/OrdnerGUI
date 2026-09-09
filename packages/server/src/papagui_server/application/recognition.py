"""Recognition orchestration with explicit review instead of silent merging."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from papagui_contracts import (
    CatalogProjectRoot,
    RecognitionCase,
    RecognitionDecisionAction,
    SourcePath,
)

from papagui_server.application.ports import (
    CatalogReaderPort,
    CustomerUnitOfWorkFactory,
    GenerationPublisherPort,
    SettingsRepositoryPort,
)
from papagui_server.domain.customer_recognition import (
    CustomerIdentityPolicy,
)
from papagui_server.application.document_recognition import DocumentRecognitionService, all_customers
from papagui_server.domain.errors import ResourceNotFoundError
from papagui_server.domain.errors import CustomerConflictError
from papagui_server.domain.folder_structure import normalize_identity
from papagui_server.domain.models import CustomerMutationResult
from papagui_server.domain.source_paths import source_uri


class RecognitionApplicationService:
    """Detect roots, persist ambiguous cases, and apply explicit decisions."""

    def __init__(
        self,
        unit_of_work: CustomerUnitOfWorkFactory,
        catalog: CatalogReaderPort,
        publisher: GenerationPublisherPort,
        identity_policy: CustomerIdentityPolicy,
        *,
        source_id: str,
        documents_per_project: int = 24,
        settings: SettingsRepositoryPort | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._catalog = catalog
        self._publisher = publisher
        self._identity_policy = identity_policy
        self._source_id = source_id
        self._documents_per_project = documents_per_project
        self._settings = settings
        self.documents = DocumentRecognitionService(unit_of_work, catalog, settings, documents_per_project)

    def synchronize(
        self, _source_path: Path, *, source_id: str, minimum_year: int,
        cancelled=lambda: False,
        exhaustive: bool = False,
    ) -> dict[str, int]:
        roots = [
            CatalogProjectRoot.from_dict(value)
            for value in self._catalog.list_project_roots(source_id=source_id)
            if int(value.get("year", 0)) >= minimum_year
        ]
        with self._unit_of_work() as work:
            run_id = work.recognition.begin_run()
            work.commit()
        stats = {
            "detected": len(roots),
            "created": 0,
            "assigned": 0,
            "pending": 0,
            "rejected": 0,
            "suggestions": 0,
        }
        try:
            with self._unit_of_work() as work:
                customers = all_customers(work.customers)
                grouped: dict[str, list[CatalogProjectRoot]] = defaultdict(list)
                for root in roots:
                    grouped[root.recognition_key].append(root)
                for key in sorted(grouped):
                    if cancelled():
                        raise InterruptedError("Kundenerkennung abgebrochen.")
                    self._process_group(work, grouped[key], customers, run_id, stats)
                    customers = all_customers(work.customers)
                work.recognition.mark_unseen_stale(run_id)
                work.commit()
            document_options = {"exhaustive": True} if exhaustive else {}
            stats["suggestions"] = self.documents.run(source_id, cancelled=cancelled, **document_options)["suggestions"]
            with self._unit_of_work() as work:
                work.recognition.finish_run(run_id, stats)
                work.commit()
        except Exception:
            with self._unit_of_work() as work:
                work.recognition.finish_run(run_id, stats, error="recognition_failed")
                work.commit()
            raise
        return stats

    def customer_project_root_ids(self, customer_id: int) -> list[int]:
        with self._unit_of_work() as work:
            if work.customers.get(customer_id) is None:
                raise ResourceNotFoundError("Kunde nicht gefunden.")
            return [int(root["id"]) for root in self._catalog.list_project_roots(source_id=self._source_id)
                    if work.projects.customer_id_for_source(SourcePath.from_dict(root["source"])) == customer_id]

    def run_now(self, *, minimum_year: int) -> dict[str, int]:
        result = self.synchronize(
            Path("."), source_id=self._source_id, minimum_year=minimum_year
        )
        self._publisher.publish_customers()
        return result

    def list_cases(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._unit_of_work() as work:
            return work.recognition.list_cases(status=status)

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._unit_of_work() as work:
            return work.recognition.list_runs(limit=limit)

    def decide(
        self,
        signature: str,
        *,
        action: str,
        customer_id: int | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> CustomerMutationResult:
        try:
            decision = RecognitionDecisionAction(action)
        except ValueError as error:
            raise ValueError("Unbekannte Erkennungsentscheidung.") from error
        key = (idempotency_key or f"recognition-reject-{signature}").strip()
        if decision is not RecognitionDecisionAction.REJECT and not idempotency_key:
            raise ValueError("Ein Idempotency-Key ist für diese Entscheidung erforderlich.")
        if decision is RecognitionDecisionAction.ACCEPT and expected_revision != 0:
            raise ValueError("expected_revision=0 ist für accept erforderlich.")
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "signature": signature,
                    "action": decision.value,
                    "customer_id": customer_id,
                    "expected_revision": expected_revision,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with self._unit_of_work() as work:
            replay = work.customers.idempotency_result(key, request_hash)
            if replay is not None:
                result = CustomerMutationResult(replay.body, replay.status_code, True)
                work.commit()
                self._publisher.publish_customers()
                return result
            case = work.recognition.get_case(signature)
            previous = work.recognition.decision(signature)
            if previous is not None:
                if previous.action is not decision or (
                    decision is RecognitionDecisionAction.ASSIGN
                    and previous.customer_id != customer_id
                ):
                    raise ValueError("Der Erkennungsfall wurde bereits anders entschieden.")
                current = (
                    work.customers.get(previous.customer_id)
                    if previous.customer_id is not None
                    else None
                )
                result = CustomerMutationResult(
                    {"decision": previous.to_dict(), "customer": current}, 200
                )
                work.customers.remember_idempotency(
                    key, request_hash, "decide_recognition_case", result
                )
                work.commit()
                self._publisher.publish_customers()
                return result
            assigned_customer_id = customer_id
            if decision is RecognitionDecisionAction.REJECT:
                assigned_customer_id = None
            elif decision is RecognitionDecisionAction.ACCEPT:
                if customer_id is not None:
                    raise ValueError("accept erstellt einen neuen Kunden ohne customer_id.")
                created = work.customers.create(
                    {
                        "display_name": case.display_name,
                        "city": "",
                        "_origin": "folder",
                        "entity_type": self._identity_policy.entity_type(
                            case.display_name
                        ),
                        "folder_path": source_uri(case.project_roots[0]),
                        "folder_paths": [source_uri(item) for item in case.project_roots],
                        "service_types": list(case.service_types),
                    }
                )
                assigned_customer_id = int(created["id"])
            else:
                current = (
                    work.customers.get(customer_id) if customer_id is not None else None
                )
                if current is None:
                    raise ResourceNotFoundError("Zielkunde nicht gefunden.")
                if expected_revision is None:
                    raise ValueError("expected_revision ist für assign erforderlich.")
                if int(current["revision"]) != expected_revision:
                    raise CustomerConflictError(current)
            if assigned_customer_id is not None:
                roots = self._roots_by_source(self._source_id)
                for source in case.project_roots:
                    root = roots.get((source.source_id, source.relative_path))
                    if root is None:
                        raise ResourceNotFoundError(
                            "Eine Projektwurzel des Erkennungsfalls existiert nicht mehr."
                        )
                    work.projects.upsert(root.to_dict(), customer_id=assigned_customer_id)
            saved = work.recognition.save_decision(
                signature,
                action=decision,
                customer_id=assigned_customer_id,
            )
            customer = (
                work.customers.get(assigned_customer_id)
                if assigned_customer_id is not None
                else None
            )
            result = CustomerMutationResult(
                {"decision": saved, "customer": customer}, 200
            )
            work.customers.remember_idempotency(
                key, request_hash, "decide_recognition_case", result
            )
            work.commit()
        self._publisher.publish_customers()
        return result

    def _process_group(
        self,
        work: Any,
        roots: list[CatalogProjectRoot],
        customers: list[dict[str, Any]],
        run_id: int,
        stats: dict[str, int],
    ) -> None:
        roots.sort(key=lambda item: (-item.year, item.source.relative_path.casefold()))
        cities = sorted({item.city for item in roots if item.city}, key=str.casefold)
        owners = {
            owner
            for root in roots
            if (owner := work.projects.customer_id_for_source(root.source)) is not None
        }
        exact = [
            item
            for item in customers
            if normalize_identity(str(item["display_name"])) == roots[0].recognition_key
        ]
        candidate_pool = exact or [
            item
            for item in customers
            if self._identity_policy.similar(
                str(item["display_name"]), roots[0].customer_name
            )
        ]
        reason = ""
        if len(cities) > 1 and not owners and not exact:
            reason = "different_cities"
        elif len(owners) > 1:
            reason = "conflicting_project_owners"
        elif len(exact) > 1:
            reason = "duplicate_exact_names"
        elif len(owners) == 1:
            owner = next(iter(owners))
            owner_customer = work.customers.get(owner)
            if owner_customer is None:
                reason = "missing_project_owner"
            else:
                self._assign(work, roots, owner, stats)
                return
        elif len(exact) == 1:
            self._assign(work, roots, int(exact[0]["id"]), stats)
            return
        elif candidate_pool:
            reason = "similar_name"
        if reason:
            case = self._case(roots, reason, owners, candidate_pool)
            if work.recognition.save_case(case, run_id=run_id):
                stats["pending"] += 1
            decision = work.recognition.decision(case.signature)
            if decision is not None and decision.action is RecognitionDecisionAction.REJECT:
                stats["rejected"] += 1
            return
        root = roots[0]
        customer = work.customers.create(
            {
                "display_name": root.customer_name,
                "city": "",
                "_origin": "folder",
                "entity_type": self._identity_policy.entity_type(root.customer_name),
                "folder_path": source_uri(root.source),
                "folder_paths": [source_uri(item.source) for item in roots],
                "service_types": sorted(
                    {item.service_type for item in roots}, key=str.casefold
                ),
            }
        )
        stats["created"] += 1
        self._assign(work, roots, int(customer["id"]), stats, count=False)

    @staticmethod
    def _assign(
        work: Any,
        roots: list[CatalogProjectRoot],
        customer_id: int,
        stats: dict[str, int],
        *,
        count: bool = True,
    ) -> None:
        changed = False
        for root in roots:
            before = work.projects.customer_id_for_source(root.source)
            work.projects.upsert(root.to_dict(), customer_id=customer_id)
            changed = changed or before is None
        if count and changed:
            stats["assigned"] += 1

    @staticmethod
    def _city_conflicts(customer_city: str, root_cities: list[str]) -> bool:
        return bool(
            customer_city.strip()
            and root_cities
            and normalize_identity(customer_city) != normalize_identity(root_cities[0])
        )

    @staticmethod
    def _case(
        roots: list[CatalogProjectRoot],
        reason: str,
        owners: set[int],
        candidates: list[dict[str, Any]],
    ) -> RecognitionCase:
        sources = tuple(
            sorted(
                (item.source for item in roots),
                key=lambda item: (item.source_id, item.relative_path.casefold()),
            )
        )
        payload = {
            "recognition_key": roots[0].recognition_key,
            "roots": [item.to_dict() for item in sources],
            "reason": reason,
        }
        signature = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        suggested = sorted(
            owners | {int(item["id"]) for item in candidates if item.get("id")}
        )
        return RecognitionCase(
            signature=signature,
            recognition_key=roots[0].recognition_key,
            display_name=roots[0].customer_name,
            project_roots=sources,
            cities=tuple(sorted({item.city for item in roots if item.city})),
            service_types=tuple(
                sorted({item.service_type for item in roots}, key=str.casefold)
            ),
            years=tuple(sorted({item.year for item in roots}, reverse=True)),
            reason=reason,
            suggested_customer_ids=tuple(suggested),
        )

    def run_for_customer(self, customer_id: int, *, cancelled=lambda: False) -> dict[str, int]:
        result = self.documents.run(self._source_id, customer_id=customer_id, cancelled=cancelled)
        self._publisher.publish_customers()
        return result

    def _roots_by_source(
        self, source_id: str
    ) -> dict[tuple[str, str], CatalogProjectRoot]:
        values = (
            CatalogProjectRoot.from_dict(item)
            for item in self._catalog.list_project_roots(source_id=source_id)
        )
        return {
            (item.source.source_id, item.source.relative_path): item for item in values
        }
