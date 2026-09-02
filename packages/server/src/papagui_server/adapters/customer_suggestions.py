"""Persistent, reviewable document suggestions with rejection memory."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any

from papagui_contracts import Contact, CustomerSuggestion, CustomerSuggestionDecision

from papagui_server.domain.errors import (
    CustomerConflictError,
    ResourceNotFoundError,
    SuggestionOverwriteError,
)
from papagui_server.domain.source_paths import coerce_source_path, source_uri


_EDITABLE_FIELDS = {
    "company",
    "email",
    "phone",
    "street",
    "postal_code",
    "city",
    "entity_type",
}


class SqliteCustomerSuggestionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        customer_id: int,
        *,
        kind: str,
        value: str,
        source_path: str,
        excerpt: str,
        fingerprint: str,
        confidence: float,
        rule: str = "document",
    ) -> bool:
        field = self._field(kind)
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("Ein Vorschlagswert darf nicht leer sein.")
        if isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
            raise ValueError("confidence muss zwischen 0 und 1 liegen.")
        source = coerce_source_path(source_path)
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO customer_document_suggestions(
                customer_id, kind, value, source_path, excerpt, fingerprint,
                confidence, rule, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                customer_id,
                field,
                cleaned,
                source_uri(source),
                str(excerpt).strip(),
                str(fingerprint).strip(),
                float(confidence),
                str(rule).strip(),
            ),
        )
        return cursor.rowcount == 1

    def list_for_customer(
        self, customer_id: int, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [customer_id]
        condition = "customer_id=?"
        if status is not None:
            normalized = status.strip().casefold()
            if normalized not in {"pending", "accepted", "rejected"}:
                raise ValueError("Unbekannter Vorschlagsstatus.")
            condition += " AND status=?"
            parameters.append(normalized)
        rows = self._connection.execute(
            "SELECT * FROM customer_document_suggestions "
            f"WHERE {condition} ORDER BY created_at, id",
            parameters,
        ).fetchall()
        return [self._map(row).to_dict() for row in rows]

    def decide(
        self,
        customer_id: int,
        suggestion_id: int,
        *,
        action: str,
        expected_revision: int | None,
        current_customer: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            decision = CustomerSuggestionDecision(action)
        except ValueError as error:
            raise ValueError("Unbekannte Vorschlagsentscheidung.") from error
        customer = current_customer
        if (
            decision is CustomerSuggestionDecision.ACCEPT
            and int(customer["revision"]) != expected_revision
        ):
            raise CustomerConflictError(customer)
        row = self._connection.execute(
            "SELECT * FROM customer_document_suggestions WHERE id=? AND customer_id=?",
            (suggestion_id, customer_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Dokumentvorschlag nicht gefunden.")
        existing_status = str(row["status"])
        target_status = (
            "accepted"
            if decision is CustomerSuggestionDecision.ACCEPT
            else "rejected"
        )
        if existing_status != "pending":
            if existing_status != target_status:
                raise ValueError("Der Dokumentvorschlag wurde bereits entschieden.")
            return self._map(row).to_dict()
        if decision is CustomerSuggestionDecision.ACCEPT:
            assert expected_revision is not None
            if str(row["suggestion_type"]) == "contact":
                inserted = self._connection.execute(
                    """
                    INSERT INTO contacts(customer_id, name, role, email, phone)
                    SELECT ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM contacts WHERE customer_id=?
                        AND name=? COLLATE NOCASE AND role=? COLLATE NOCASE
                        AND email=? COLLATE NOCASE AND phone=? COLLATE NOCASE
                    )
                    """,
                    (
                        customer_id,
                        str(row["contact_name"]),
                        str(row["contact_role"]),
                        str(row["contact_email"]),
                        str(row["contact_phone"]),
                        customer_id,
                        str(row["contact_name"]),
                        str(row["contact_role"]),
                        str(row["contact_email"]),
                        str(row["contact_phone"]),
                    ),
                ).rowcount
                if inserted:
                    self._increment_revision(customer_id, expected_revision, customer)
            else:
                field = self._field(str(row["kind"]))
                current_value = str(customer[field]).strip()
                suggested = str(row["value"]).strip()
                if current_value and current_value.casefold() != suggested.casefold():
                    raise SuggestionOverwriteError(customer)
                if not current_value:
                    cursor = self._connection.execute(
                        f"UPDATE customers SET {field}=?, revision=revision+1, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND revision=?",
                        (suggested, customer_id, expected_revision),
                    )
                    if cursor.rowcount != 1:
                        raise CustomerConflictError(current_customer)
        self._connection.execute(
            "UPDATE customer_document_suggestions SET status=?, resolved_at=? "
            "WHERE id=? AND status='pending'",
            (
                target_status,
                datetime.now(timezone.utc).isoformat(),
                suggestion_id,
            ),
        )
        updated = self._connection.execute(
            "SELECT * FROM customer_document_suggestions WHERE id=?", (suggestion_id,)
        ).fetchone()
        assert updated is not None
        return self._map(updated).to_dict()

    @staticmethod
    def _field(value: str) -> str:
        field = str(value).strip()
        if field not in _EDITABLE_FIELDS:
            raise ValueError("Dieses Kundenfeld kann nicht vorgeschlagen werden.")
        return field

    @staticmethod
    def _map(row: sqlite3.Row) -> CustomerSuggestion:
        contact = None
        if str(row["suggestion_type"]) == "contact":
            contact = Contact(
                name=str(row["contact_name"]),
                role=str(row["contact_role"]),
                email=str(row["contact_email"]),
                phone=str(row["contact_phone"]),
            )
        return CustomerSuggestion(
            id=int(row["id"]),
            customer_id=int(row["customer_id"]),
            field_name=str(row["kind"]),
            value=str(row["value"]),
            source=coerce_source_path(str(row["source_path"])),
            fingerprint=str(row["fingerprint"]),
            excerpt=str(row["excerpt"]),
            rule=str(row["rule"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            suggestion_type=str(row["suggestion_type"]),
            contact=contact,
            created_at=str(row["created_at"]),
            resolved_at=str(row["resolved_at"]),
        )

    def _increment_revision(
        self,
        customer_id: int,
        expected_revision: int,
        current_customer: dict[str, Any],
    ) -> None:
        cursor = self._connection.execute(
            "UPDATE customers SET revision=revision+1, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND revision=?",
            (customer_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise CustomerConflictError(current_customer)
