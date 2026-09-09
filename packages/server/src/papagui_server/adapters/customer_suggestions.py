"""Canonical review decisions with independent, versioned document evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid
from typing import Any

from papagui_contracts import Contact, CustomerSuggestion
from papagui_server.adapters.candidate_migration import (
    canonical_value, candidate_key, migrate_candidates,
)
from papagui_server.adapters.candidate_schema import record_provenance
from papagui_server.adapters.candidate_validation import revalidate_pending_candidates, valid_suggestion
from papagui_server.adapters.recognition_blocklist import SqliteRecognitionBlocklistRepository
from papagui_server.domain.errors import (
    CustomerConflictError, ResourceNotFoundError, SuggestionOverwriteError,
)
from papagui_server.domain.source_paths import coerce_source_path, source_uri

_EDITABLE_FIELDS = {"company", "email", "phone", "street", "postal_code", "city", "entity_type"}
_REJECTION_REASONS = {"", "not_a_value", "wrong_customer", "outdated", "already_present", "other"}


class SqliteCustomerSuggestionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        migrate_candidates(connection)
        revalidate_pending_candidates(connection)

    def add(self, customer_id: int, *, kind: str, value: str, source_path: str,
            excerpt: str, fingerprint: str, confidence: float, rule: str = "document",
            normalized_value: str = "", party_key: str = "customer",
            party_role: str = "unknown", quality: str = "legacy", reasons=(),
            suggestion_type: str = "field", payload: dict | None = None,
            source_locator: dict | None = None, document_hash: str = "", document_family: str = "",
            engine_version: str = "", run_id: str = "") -> bool:
        kind = self._field(kind)
        value = str(value).strip()
        if not value:
            raise ValueError("Ein Vorschlagswert darf nicht leer sein.")
        if isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
            raise ValueError("confidence muss zwischen 0 und 1 liegen.")
        if quality not in {"strong", "review", "legacy"}:
            raise ValueError("Unbekannte Vorschlagsqualität.")
        payload = dict(payload or {})
        if kind == "contact":
            suggestion_type = "contact"
        if not valid_suggestion("contact" if suggestion_type == "contact" else kind, value, payload):
            return False
        normalized = canonical_value(kind, value, payload)
        source = source_uri(coerce_source_path(source_path))
        locator_json = json.dumps(source_locator or {}, sort_keys=True)
        # Group equivalence is stable across detector versions and file names.
        key = candidate_key(customer_id, kind, normalized, party_key)
        alias = self._connection.execute(
            "SELECT candidate_id FROM candidate_aliases WHERE fingerprint=?", (key,)
        ).fetchone()
        # Legacy customer-wide rejection applies even when a formerly unknown
        # party gains a precise identity. Decisions never cross customer IDs.
        if alias is None:
            alias = self._connection.execute(
                "SELECT id FROM customer_document_suggestions WHERE customer_id=? "
                "AND kind=? AND normalized_value=? AND canonical_id IS NULL "
                "AND (party_key=? OR (status<>'pending' AND (engine_version='' OR EXISTS ("
                "SELECT 1 FROM candidate_decisions d WHERE d.candidate_id=customer_document_suggestions.id "
                "AND d.scope='customer_value')))) "
                "ORDER BY CASE status WHEN 'accepted' THEN 0 WHEN 'rejected' THEN 1 ELSE 2 END,id LIMIT 1",
                (customer_id, kind, normalized, party_key),
            ).fetchone()
        resolved_party = False
        if alias is None and quality == "strong" and party_role == "customer":
            prior = self._connection.execute(
                "SELECT DISTINCT s.id FROM customer_document_suggestions s "
                "JOIN candidate_evidence e ON e.candidate_id=s.id "
                "WHERE s.customer_id=? AND s.kind=? AND s.normalized_value=? "
                "AND s.status='pending' AND s.canonical_id IS NULL AND s.party_key LIKE 'unresolved:%' "
                "AND e.source_path=? AND e.locator_json=? AND e.active=1 LIMIT 2",
                (customer_id, kind, normalized, source, locator_json),
            ).fetchall()
            if len(prior) == 1:
                alias = prior[0]
                resolved_party = True
        contact = payload if suggestion_type == "contact" else {}
        created = alias is None
        if created:
            cursor = self._connection.execute(
                """INSERT INTO customer_document_suggestions
                    (customer_id,kind,value,source_path,excerpt,fingerprint,confidence,rule,
                     normalized_value,party_key,party_role,quality,reasons_json,payload_json,
                     suggestion_type,contact_name,contact_role,contact_email,contact_phone,
                     engine_version,last_seen_run)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (customer_id, kind, value, source, excerpt[:1000], key, confidence, rule,
                 normalized, party_key, party_role, quality, json.dumps(list(reasons)),
                 json.dumps(payload, ensure_ascii=False), suggestion_type,
                 *(contact.get(field, "") for field in ("name", "role", "email", "phone")),
                 engine_version, str(run_id)),
            )
            candidate_id = cursor.lastrowid
        else:
            candidate_id = int(alias[0])
            if resolved_party:
                self._connection.execute(
                    "UPDATE customer_document_suggestions SET party_key=? WHERE id=?",
                    (party_key, candidate_id),
                )
            self._connection.execute(
                "UPDATE customer_document_suggestions SET last_seen_run=?,engine_version=?,"
                "lifecycle=CASE WHEN status='pending' THEN 'active' ELSE lifecycle END,"
                "quality=?,"
                "party_role=?,reasons_json=? WHERE id=?",
                (str(run_id), engine_version, quality, party_role, json.dumps(list(reasons)), candidate_id),
            )
        for alias_key in (key, fingerprint):
            if alias_key:
                self._connection.execute(
                    "INSERT OR IGNORE INTO candidate_aliases VALUES (?,?)", (alias_key, candidate_id)
                )
        self._connection.execute(
            """INSERT INTO candidate_evidence(candidate_id,source_path,document_hash,
                locator_json,excerpt,active,last_seen_run,quality,party_role,reasons_json,engine_version,document_family)
                VALUES (?,?,?,?,?,1,?,?,?,?,?,?)
               ON CONFLICT(candidate_id,source_path,locator_json) DO UPDATE SET
                document_hash=excluded.document_hash,excerpt=excluded.excerpt,
                active=1,last_seen_run=excluded.last_seen_run,quality=excluded.quality,
                party_role=excluded.party_role,reasons_json=excluded.reasons_json,
                engine_version=excluded.engine_version,document_family=excluded.document_family""",
            (candidate_id, source, document_hash,
             locator_json, excerpt[:1000], str(run_id),
             quality, party_role, json.dumps(list(reasons)), engine_version, document_family),
        )
        self._refresh_assessment(candidate_id)
        if SqliteRecognitionBlocklistRepository(self._connection).matches(kind, value, payload):
            self._connection.execute(
                "UPDATE customer_document_suggestions SET lifecycle='blocked' WHERE id=? AND status='pending'",
                (candidate_id,),
            )
            return False
        return created

    def _refresh_assessment(self, candidate_id):
        evidence = self._connection.execute(
            "SELECT quality,party_role,reasons_json FROM candidate_evidence "
            "WHERE candidate_id=? AND active=1 ORDER BY "
            "CASE quality WHEN 'strong' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,id",
            (candidate_id,),
        ).fetchall()
        quality = str(evidence[0]["quality"]) if evidence else "review"
        best = [row for row in evidence if row["quality"] == quality]
        roles = {str(row["party_role"]) for row in best}
        role = next(iter(roles)) if len(roles) == 1 else "unknown"
        reasons = {str(reason) for row in best for reason in json.loads(row["reasons_json"])}
        if not evidence:
            reasons.add("no-active-evidence")
        if len(roles) > 1:
            quality = "review"
            reasons.add("party-assignment-conflict")
        # A source refresh must not erase a conflict between historical decisions.
        decisions = self._connection.execute(
            "SELECT count(DISTINCT action) FROM candidate_decisions WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()[0]
        if decisions > 1:
            quality = "review"
            reasons.add("legacy_decision_conflict")
        self._connection.execute(
            "UPDATE customer_document_suggestions SET quality=?,party_role=?,reasons_json=? WHERE id=?",
            (quality, role, json.dumps(sorted(reasons)), candidate_id),
        )

    def list_for_customer(self, customer_id: int, *, status: str | None = None,
                          limit: int | None = None, offset: int = 0,
                          include_groups: bool = True) -> list[dict[str, Any]]:
        condition, args = self._condition(customer_id, status, include_groups)
        query = "SELECT * FROM customer_document_suggestions WHERE " + condition
        query += " ORDER BY CASE quality WHEN 'strong' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, kind,id"
        if limit is not None:
            if isinstance(limit, bool) or not 1 <= limit <= 500 or offset < 0:
                raise ValueError("Ungültige Seitengröße.")
            query += " LIMIT ? OFFSET ?"
            args.extend((limit, offset))
        rows = self._connection.execute(query, args).fetchall()
        return [self._map(row).to_dict() for row in rows]

    def count_for_customer(self, customer_id, *, status=None, include_groups=True):
        condition, args = self._condition(customer_id, status, include_groups)
        return int(self._connection.execute(
            "SELECT count(*) FROM customer_document_suggestions WHERE " + condition, args
        ).fetchone()[0])

    @staticmethod
    def _condition(customer_id, status, include_groups):
        condition = "customer_id=? AND canonical_id IS NULL"
        args = [customer_id]
        if status is not None:
            if status not in {"pending", "accepted", "rejected"}:
                raise ValueError("Unbekannter Vorschlagsstatus.")
            condition += " AND status=?"
            args.append(status)
        if status in {None, "pending"}:
            condition += " AND (status<>'pending' OR lifecycle='active')"
        if not include_groups:
            condition += " AND suggestion_type<>'address'"
        return condition, args

    def decide(self, customer_id: int, suggestion_id: int, *, action: str,
               expected_revision: int | None, current_customer: dict[str, Any],
               reason: str = "") -> dict[str, Any]:
        if action not in {"accept", "reject"} or reason not in _REJECTION_REASONS:
            raise ValueError("Unbekannte Vorschlagsentscheidung.")
        row = self._connection.execute(
            "SELECT * FROM customer_document_suggestions WHERE id=? AND customer_id=?",
            (suggestion_id, customer_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Dokumentvorschlag nicht gefunden.")
        if row["canonical_id"] is not None:
            row = self._connection.execute(
                "SELECT * FROM customer_document_suggestions WHERE id=?", (row["canonical_id"],)
            ).fetchone()
        candidate_id = int(row["id"])
        target = "accepted" if action == "accept" else "rejected"
        if action == "accept" and current_customer["revision"] != expected_revision:
            raise CustomerConflictError(current_customer)
        if row["status"] != "pending":
            if row["status"] != target:
                raise ValueError("Der Dokumentvorschlag wurde bereits entschieden.")
            return self._map(row).to_dict()
        if row["lifecycle"] != "active":
            raise ValueError("Der Vorschlag ist nicht mehr aktuell. Bitte neu laden.")
        if action == "accept":
            if not valid_suggestion(
                "contact" if row["suggestion_type"] == "contact" else row["kind"],
                row["value"], self._values(row),
            ):
                raise ValueError("Dieser Vorschlag enthält keine gültigen Kontaktdaten. Bitte neu bewerten.")
            self._accept(row, current_customer, expected_revision)
        self._connection.execute(
            "UPDATE customer_document_suggestions SET status=?,resolved_at=? WHERE id=?",
            (target, datetime.now(timezone.utc).isoformat(), candidate_id),
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO candidate_decisions(candidate_id,original_suggestion_id,action,reason) "
            "VALUES (?,?,?,?)", (candidate_id, suggestion_id, target, reason)
        )
        self._apply_customer_value_scope(row, candidate_id)
        updated = self._connection.execute(
            "SELECT * FROM customer_document_suggestions WHERE id=?", (candidate_id,)
        ).fetchone()
        return self._map(updated).to_dict()

    def _apply_customer_value_scope(self, row, candidate_id):
        """An explicit customer/value decision also resolves existing duplicate cards."""
        duplicates = self._connection.execute(
            "SELECT id FROM customer_document_suggestions WHERE customer_id=? AND kind=? "
            "AND normalized_value=? AND id<>? AND status='pending' AND canonical_id IS NULL",
            (row["customer_id"], row["kind"], row["normalized_value"], candidate_id),
        ).fetchall()
        evidence_fields = "source_path,document_hash,document_family,locator_json,excerpt,active,last_seen_run,quality,party_role,reasons_json,engine_version"
        for duplicate in duplicates:
            other_id = int(duplicate["id"])
            self._connection.execute(
                f"INSERT OR IGNORE INTO candidate_evidence(candidate_id,{evidence_fields}) "
                f"SELECT ?,{evidence_fields} FROM candidate_evidence WHERE candidate_id=?",
                (candidate_id, other_id),
            )
            self._connection.execute("UPDATE candidate_evidence SET active=0 WHERE candidate_id=?", (other_id,))
            self._connection.execute(
                "UPDATE customer_document_suggestions SET canonical_id=?,lifecycle='superseded' WHERE id=?",
                (candidate_id, other_id),
            )
            self._connection.execute("UPDATE candidate_aliases SET candidate_id=? WHERE candidate_id=?", (candidate_id, other_id))
        self._refresh_assessment(candidate_id)

    def _accept(self, row, customer, revision):
        customer_id = int(customer["id"])
        payload = self._values(row)
        if row["suggestion_type"] == "address" and not all(
            payload.get(key) for key in ("street", "postal_code", "city")
        ):
            raise ValueError("Die Anschrift ist unvollständig. Bitte manuell ergänzen.")
        if row["suggestion_type"] == "contact":
            contacts = customer.get("contacts", [])
            match = next((item for item in contacts
                          if self._contact_matches(item, payload)), None)
            if match is not None:
                return
            named = [item for item in contacts if self._same_contact_name(item, payload)]
            if self._contact_conflicts(contacts, payload):
                raise SuggestionOverwriteError(customer)
            if named:
                match = named[0]
                uid = str(match.get("id", ""))
                if not uid:
                    raise SuggestionOverwriteError(customer)
                changes = {key: value for key, value in payload.items() if value and not match.get(key)}
                assignments = ",".join(f"{key}=?" for key in changes)
                cursor = self._connection.execute(
                    f"UPDATE contacts SET {assignments} WHERE customer_id=? AND uid=?",
                    (*changes.values(), customer_id, uid),
                )
                if cursor.rowcount != 1:
                    raise SuggestionOverwriteError(customer)
            else:
                uid = uuid.uuid4().hex
                changes = {key: value for key, value in payload.items() if value}
                self._connection.execute(
                    "INSERT INTO contacts(customer_id,uid,name,role,email,phone) VALUES (?,?,?,?,?,?)",
                    (customer_id, uid, *(payload.get(k, "") for k in ("name", "role", "email", "phone"))),
                )
            self._increment_revision(customer_id, revision, customer)
            record_provenance(self._connection, customer_id, changes,
                              origin="confirmed", candidate_id=row["id"], target_id=uid)
            return
        # An address group is one atomic decision. Check every part before writing.
        for field, proposed in payload.items():
            field = self._field(field)
            existing = str(customer.get(field, "")).strip()
            if existing and canonical_value(field, existing) != canonical_value(field, proposed):
                raise SuggestionOverwriteError(customer)
        changes = {key: value for key, value in payload.items() if value and not customer.get(key)}
        if changes:
            assignments = ",".join(f"{self._field(key)}=?" for key in changes)
            self._connection.execute(
                f"UPDATE customers SET {assignments} WHERE id=?", (*changes.values(), customer_id)
            )
            self._increment_revision(customer_id, revision, customer)
            record_provenance(self._connection, customer_id, changes,
                              origin="confirmed", candidate_id=row["id"])

    @staticmethod
    def _contact_matches(existing, proposed):
        return all(canonical_value(key, str(existing.get(key, ""))) == canonical_value(key, str(value))
                   for key, value in proposed.items() if value and key != "id")

    @staticmethod
    def _same_contact_name(existing, proposed):
        name = str(proposed.get("name", "")).strip()
        return bool(name) and canonical_value("name", name) == canonical_value("name", str(existing.get("name", "")))

    @classmethod
    def _contact_conflicts(cls, contacts, proposed):
        if any(cls._contact_matches(item, proposed) for item in contacts):
            return False
        named = [item for item in contacts if cls._same_contact_name(item, proposed)]
        if len(named) > 1:
            return True
        return bool(named) and any(
            named[0].get(key) and value and canonical_value(key, str(named[0][key])) != canonical_value(key, str(value))
            for key, value in proposed.items() if key in {"name", "role", "email", "phone"}
        )

    @staticmethod
    def _values(row):
        if row["suggestion_type"] in {"address", "contact"}:
            payload = json.loads(row["payload_json"])
            if payload:
                fields = {"street", "postal_code", "city"} if row["suggestion_type"] == "address" else {"name", "role", "email", "phone"}
                return {key: value for key, value in payload.items() if key in fields}
            if row["suggestion_type"] == "contact":
                return {k: str(row[f"contact_{k}"]) for k in ("name", "role", "email", "phone")}
        return {str(row["kind"]): str(row["value"])}

    def reconcile_customer(self, customer):
        rows = self._connection.execute(
            "SELECT * FROM customer_document_suggestions WHERE customer_id=? "
            "AND status='pending' AND canonical_id IS NULL AND lifecycle IN ('active','satisfied')",
            (customer["id"],),
        ).fetchall()
        for row in rows:
            values = self._values(row)
            satisfied = (any(self._contact_matches(c, values) for c in customer.get("contacts", []))
                         if row["suggestion_type"] == "contact" else all(
                             customer.get(k) and canonical_value(k, str(customer[k])) == canonical_value(k, str(v))
                             for k, v in values.items() if v))
            self._connection.execute(
                "UPDATE customer_document_suggestions SET lifecycle=? WHERE id=?",
                ("satisfied" if satisfied else "active", row["id"]),
            )

    def finalize_run(self, customer_id, run_id, *, evaluated_sources, existing_sources=None):
        rows = self._connection.execute(
            "SELECT e.id,e.candidate_id,e.source_path,e.last_seen_run FROM candidate_evidence e "
            "JOIN customer_document_suggestions s ON s.id=e.candidate_id WHERE s.customer_id=?",
            (customer_id,),
        ).fetchall()
        evaluated = set(evaluated_sources)
        existing = set(existing_sources) if existing_sources is not None else None
        changed = set()
        for row in rows:
            if (row["source_path"] in evaluated and row["last_seen_run"] != str(run_id)) or (
                existing is not None and row["source_path"] not in existing
            ):
                self._connection.execute("UPDATE candidate_evidence SET active=0 WHERE id=?", (row["id"],))
                changed.add(int(row["candidate_id"]))
        for candidate_id in changed:
            self._refresh_assessment(candidate_id)
        self._connection.execute(
            "UPDATE customer_document_suggestions SET lifecycle='stale' WHERE customer_id=? "
            "AND status='pending' AND canonical_id IS NULL AND lifecycle IN ('active','satisfied') "
            "AND NOT EXISTS (SELECT 1 FROM candidate_evidence e WHERE e.candidate_id="
            "customer_document_suggestions.id AND e.active=1)", (customer_id,)
        )

    def set_recognition_status(self, customer_id, *, state, reason, counts,
                               pipeline_version, catalog_version=""):
        self._connection.execute(
            """INSERT INTO customer_recognition_status(customer_id,state,reason,counts_json,
                last_run_at,pipeline_version,catalog_version) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(customer_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,
                counts_json=excluded.counts_json,last_run_at=excluded.last_run_at,
                pipeline_version=excluded.pipeline_version,catalog_version=excluded.catalog_version""",
            (customer_id, state, reason, json.dumps(counts), datetime.now(timezone.utc).isoformat(),
             pipeline_version, catalog_version),
        )

    def recognition_status(self, customer_id):
        row = self._connection.execute(
            "SELECT * FROM customer_recognition_status WHERE customer_id=?", (customer_id,)
        ).fetchone()
        result = {"customer_id": customer_id, "state": "not_evaluated", "reason": "not_evaluated",
                  "counts": {}, "last_run_at": "", "pipeline_version": ""}
        if row:
            result.update({key: row[key] for key in ("state", "reason", "last_run_at", "pipeline_version")})
            result["counts"] = json.loads(row["counts_json"])
        result["counts"]["open"] = self.count_for_customer(customer_id, status="pending")
        result["counts"]["migration_conflicts"] = self._connection.execute(
            "SELECT count(*) FROM customer_document_suggestions WHERE customer_id=? "
            "AND reasons_json LIKE '%legacy_decision_conflict%'", (customer_id,)
        ).fetchone()[0]
        return result

    @staticmethod
    def _field(value):
        value = str(value).strip()
        if value not in _EDITABLE_FIELDS | {"address", "contact"}:
            raise ValueError("Dieses Kundenfeld kann nicht vorgeschlagen werden.")
        return value

    def _map(self, row):
        evidence_rows = self._connection.execute(
            "SELECT * FROM candidate_evidence WHERE candidate_id=? AND active=1 ORDER BY "
            "CASE quality WHEN 'strong' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,id LIMIT 50",
            (row["id"],),
        ).fetchall()
        evidence = tuple({
            **json.loads(item["locator_json"]),
            "source": coerce_source_path(item["source_path"]).to_dict(),
            "excerpt": item["excerpt"],
        } for item in evidence_rows)
        # Copies of one document provide one independent supporting source.
        count = self._connection.execute(
            "SELECT count(DISTINCT CASE WHEN document_family<>'' THEN 'family:'||document_family "
            "WHEN document_hash<>'' THEN 'hash:'||document_hash ELSE 'source:'||source_path END) "
            "FROM candidate_evidence WHERE candidate_id=? AND active=1", (row["id"],)
        ).fetchone()[0]
        payload = json.loads(row["payload_json"])
        contact = Contact.from_dict(self._values(row)) if row["suggestion_type"] == "contact" else None
        customer = self._connection.execute("SELECT * FROM customers WHERE id=?", (row["customer_id"],)).fetchone()
        conflict = row["suggestion_type"] != "contact" and any(
            customer[k] and canonical_value(k, str(customer[k])) != canonical_value(k, str(v))
            for k, v in self._values(row).items() if v and k in _EDITABLE_FIELDS
        )
        if row["suggestion_type"] == "contact":
            contacts = [dict(item) for item in self._connection.execute(
                "SELECT name,role,email,phone FROM contacts WHERE customer_id=?",
                (row["customer_id"],),
            )]
            conflict = self._contact_conflicts(contacts, self._values(row))
        source = coerce_source_path(evidence_rows[0]["source_path"] if evidence_rows else row["source_path"])
        return CustomerSuggestion(
            id=int(row["id"]), customer_id=int(row["customer_id"]), field_name=str(row["kind"]),
            value=str(row["value"]), source=source, fingerprint=str(row["fingerprint"]),
            excerpt=str(evidence_rows[0]["excerpt"] if evidence_rows else row["excerpt"]),
            rule=str(row["rule"]), confidence=float(row["confidence"]),
            status=str(row["status"]), suggestion_type=str(row["suggestion_type"]), contact=contact,
            created_at=str(row["created_at"]), resolved_at=str(row["resolved_at"]),
            normalized_value=str(row["normalized_value"]), party_role=str(row["party_role"]),
            quality=str(row["quality"]), reasons=tuple(json.loads(row["reasons_json"])),
            payload=payload, evidence=evidence, evidence_count=int(count), is_conflict=bool(conflict),
        )

    def _increment_revision(self, customer_id, expected_revision, current_customer):
        cursor = self._connection.execute(
            "UPDATE customers SET revision=revision+1,updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND revision=?", (customer_id, expected_revision)
        )
        if cursor.rowcount != 1:
            raise CustomerConflictError(current_customer)
