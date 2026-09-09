"""Global exact-value exclusions for document recognition, without changing decisions."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email

from papagui_server.domain.recognition_values import normalize_candidate_value


_KINDS = {"email", "phone", "email_domain", "contact_name", "company"}
_BATCH_LIMIT = 500
_PUBLICATION_PENDING_KEY = "blocklist_publication_pending"
_ID_HIGHWATER_KEY = "blocklist_id_highwater"


def _domain(value: str) -> str:
    """Validate one exact domain offline and compare its IDNA representation."""
    if not value or any(char in value for char in "@/*?\\:#[]"):
        return ""
    try:
        parsed = validate_email("blocklist@" + value, check_deliverability=False)
    except EmailNotValidError:
        return ""
    return str(parsed.ascii_domain or "").casefold()


def _normalize(kind: str, value: str) -> str:
    return _domain(value) if kind == "email_domain" else normalize_candidate_value(kind, value)


def _keys(kind: str, value: str, payload: dict[str, Any] | None) -> set[tuple[str, str]]:
    values: dict[str, str] = {}
    if kind == "contact":
        if not payload:
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                parsed = {}
            payload = parsed if isinstance(parsed, dict) else {}
        for field, block_kind in (("name", "contact_name"), ("email", "email"),
                                  ("phone", "phone"), ("company", "company")):
            raw = payload.get(field)
            if isinstance(raw, str) and raw.strip():
                values[block_kind] = raw
    elif kind in _KINDS:
        values[kind] = value
    keys = {(key, normalized) for key, raw in values.items()
            if (normalized := _normalize(key, raw))}
    email = values.get("email", "")
    if normalize_candidate_value("email", email):
        domain = _domain(email.rsplit("@", 1)[-1])
        if domain:
            keys.add(("email_domain", domain))
    return keys


class SqliteRecognitionBlocklistRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._restored_customer_ids: set[int] = set()

    @property
    def restored_customer_ids(self) -> tuple[int, ...]:
        """Customers whose candidates were restored by the latest reconciliation."""
        return tuple(sorted(self._restored_customer_ids))

    def list(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT id,kind,value,normalized_value,reason,created_at "
            "FROM recognition_blocklist ORDER BY kind,normalized_value,id"
        ).fetchall()
        return [dict(row) for row in rows]

    def add(self, kind: str, value: str, reason: str = "") -> dict[str, Any]:
        kind, value, normalized, reason = self._validated_entry(kind, value, reason)
        self._insert(kind, value, normalized, reason)
        self.apply_pending()
        row = self._connection.execute(
            "SELECT id,kind,value,normalized_value,reason,created_at "
            "FROM recognition_blocklist WHERE kind=? AND normalized_value=?", (kind, normalized)
        ).fetchone()
        return dict(row)

    @staticmethod
    def _validated_entry(kind: str, value: str, reason: str) -> tuple[str, str, str, str]:
        if not isinstance(kind, str) or kind.strip().casefold() not in _KINDS:
            raise ValueError("Unbekannte Art des Ausschlusses.")
        kind = kind.strip().casefold()
        if not isinstance(value, str) or not value.strip() or len(value) > 320:
            raise ValueError("Ein gültiger Ausschlusswert ist erforderlich.")
        value = value.strip()
        normalized = _normalize(kind, value)
        if not normalized:
            raise ValueError("Der Ausschlusswert ist für diese Art ungültig.")
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("Die Begründung darf höchstens 500 Zeichen enthalten.")
        return kind, value, normalized, reason.strip()

    def _insert(self, kind: str, value: str, normalized: str, reason: str) -> bool:
        if self._connection.execute(
            "SELECT 1 FROM recognition_blocklist WHERE kind=? AND normalized_value=?",
            (kind, normalized),
        ).fetchone() is not None:
            return False
        entry_id = self._id_highwater() + 1
        if entry_id > (1 << 63) - 1:
            raise ValueError("Keine weiteren Ausschluss-IDs verfügbar.")
        self._connection.execute(
            "INSERT OR REPLACE INTO candidate_schema_metadata(key,value) VALUES (?,?)",
            (_ID_HIGHWATER_KEY, str(entry_id)),
        )
        return self._connection.execute(
            "INSERT INTO recognition_blocklist(id,kind,value,normalized_value,reason) VALUES (?,?,?,?,?) "
            "ON CONFLICT(kind,normalized_value) DO NOTHING",
            (entry_id, kind, value, normalized, reason),
        ).rowcount > 0

    def _id_highwater(self) -> int:
        """Preserve deleted IDs so stale deltas never target unrelated additions."""
        row = self._connection.execute(
            "SELECT value FROM candidate_schema_metadata WHERE key=?", (_ID_HIGHWATER_KEY,)
        ).fetchone()
        previous = int(row[0]) if row is not None else 0
        current = int(self._connection.execute(
            "SELECT coalesce(max(id),0) FROM recognition_blocklist"
        ).fetchone()[0])
        if current > previous:
            self._connection.execute(
                "INSERT OR REPLACE INTO candidate_schema_metadata(key,value) VALUES (?,?)",
                (_ID_HIGHWATER_KEY, str(current)),
            )
        return max(previous, current)

    def apply_batch(self, additions: list[dict[str, Any]], deletions: list[int]) -> bool:
        """Apply a validated delta in the caller's transaction and reconcile once."""
        if (not isinstance(additions, list) or not isinstance(deletions, list)
                or len(additions) > _BATCH_LIMIT or len(deletions) > _BATCH_LIMIT):
            raise ValueError("Pro Vorgang sind höchstens 500 Ergänzungen und 500 Löschungen möglich.")
        validated: dict[tuple[str, str], tuple[str, str, str, str]] = {}
        for index, addition in enumerate(additions, start=1):
            if not isinstance(addition, dict) or set(addition) - {"kind", "value", "reason"}:
                raise ValueError(f"Sperre {index}: Ungültiger Ausschluss.")
            try:
                entry = self._validated_entry(
                    addition.get("kind"), addition.get("value"), addition.get("reason", "")
                )
            except ValueError as error:
                raise ValueError(f"Sperre {index}: {error}") from error
            validated.setdefault((entry[0], entry[2]), entry)
        for entry_id in deletions:
            self._validate_id(entry_id)
        # Validate every value before executing the first mutation. Missing IDs
        # and existing canonical values are harmless when a request is retried.
        if deletions:
            # Capture the maximum before deleting, including databases created
            # by older versions which do not yet have a durable high-water mark.
            self._id_highwater()
        changed = False
        for entry_id in dict.fromkeys(deletions):
            deleted = self._connection.execute(
                "DELETE FROM recognition_blocklist WHERE id=?", (entry_id,)
            ).rowcount > 0
            changed = deleted or changed
        for entry in validated.values():
            inserted = self._insert(*entry)
            changed = inserted or changed
        self._restored_customer_ids.clear()
        if changed:
            self.apply_pending()
        return changed

    def pending_publication(self) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM candidate_schema_metadata WHERE key=?", (_PUBLICATION_PENDING_KEY,)
        ).fetchone()
        return str(row[0]) if row is not None else None

    def mark_publication_pending(self) -> str:
        """Persist a retry marker atomically with the exclusion changes."""
        token = uuid4().hex
        self._connection.execute(
            "INSERT OR REPLACE INTO candidate_schema_metadata(key,value) VALUES (?,?)",
            (_PUBLICATION_PENDING_KEY, token),
        )
        return token

    def clear_pending_publication(self, token: str) -> None:
        # A publication must not acknowledge a newer concurrent transaction.
        self._connection.execute(
            "DELETE FROM candidate_schema_metadata WHERE key=? AND value=?",
            (_PUBLICATION_PENDING_KEY, token),
        )

    @staticmethod
    def _validate_id(entry_id: int) -> None:
        if (isinstance(entry_id, bool) or not isinstance(entry_id, int)
                or not 1 <= entry_id <= (1 << 63) - 1):
            raise ValueError("Ungültige Ausschluss-ID.")

    def delete(self, entry_id: int) -> bool:
        self._validate_id(entry_id)
        self._id_highwater()
        deleted = self._connection.execute(
            "DELETE FROM recognition_blocklist WHERE id=?", (entry_id,)
        ).rowcount > 0
        if deleted:
            self.apply_pending()
        return deleted

    def matches(self, kind: str, value: str, payload: dict[str, Any] | None = None) -> bool:
        keys = _keys(kind, value, payload)
        return any(self._connection.execute(
            "SELECT 1 FROM recognition_blocklist WHERE kind=? AND normalized_value=?", key
        ).fetchone() is not None for key in keys)

    def apply_pending(self, customer_id: int | None = None) -> int:
        """Reconcile exclusions in the caller's transaction, retaining all evidence."""
        self._restored_customer_ids.clear()
        entries = {(row["kind"], row["normalized_value"]) for row in self._connection.execute(
            "SELECT kind,normalized_value FROM recognition_blocklist"
        )}
        query = (
            "SELECT s.*, EXISTS(SELECT 1 FROM candidate_evidence e "
            "WHERE e.candidate_id=s.id AND e.active=1) AS has_active_evidence "
            "FROM customer_document_suggestions s WHERE s.status='pending' "
            "AND s.canonical_id IS NULL AND s.lifecycle IN ('active','satisfied','blocked')"
        )
        args: tuple[int, ...] = ()
        if customer_id is not None:
            query += " AND s.customer_id=?"
            args = (customer_id,)
        changed = 0
        for row in self._connection.execute(query, args).fetchall():
            try:
                payload = json.loads(row["payload_json"])
            except (ValueError, TypeError):
                payload = {}
            payload = payload if isinstance(payload, dict) else {}
            kind = str(row["kind"])
            if row["suggestion_type"] == "contact":
                kind = "contact"
                payload = {key: payload.get(key) or row[f"contact_{key}"]
                           for key in ("name", "role", "email", "phone")}
            blocked = bool(entries.intersection(_keys(kind, str(row["value"]), payload)))
            lifecycle = str(row["lifecycle"])
            if blocked:
                updated = "blocked"
            elif lifecycle == "blocked":
                updated = "active" if row["has_active_evidence"] else "stale"
            else:
                continue
            if updated != lifecycle:
                self._connection.execute(
                    "UPDATE customer_document_suggestions SET lifecycle=? WHERE id=?",
                    (updated, row["id"]),
                )
                changed += 1
                if updated == "active":
                    self._restored_customer_ids.add(int(row["customer_id"]))
        return changed
