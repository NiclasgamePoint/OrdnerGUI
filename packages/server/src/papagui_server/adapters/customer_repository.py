"""Focused SQLite repository for customer and journal aggregates."""

from __future__ import annotations

from collections.abc import Iterable
import sqlite3
from typing import Any
import uuid

from papagui_server.adapters.customer_mapping import (
    CUSTOMER_FIELDS,
    SqliteCustomerMapper,
    normalized_customer,
    unique_strings,
)
from papagui_server.adapters.customer_projects import SqliteCustomerProjectRepository
from papagui_server.adapters.customer_suggestions import (
    SqliteCustomerSuggestionRepository,
)
from papagui_server.adapters.idempotency import SqliteIdempotencyRepository
from papagui_server.domain.errors import (
    CustomerConflictError,
    ResourceNotFoundError,
)
from papagui_server.domain.models import CustomerMutationResult


class SqliteCustomerRepository:
    """Map the existing v0.4 customer schema without leaking a connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        projects: SqliteCustomerProjectRepository | None = None,
        suggestions: SqliteCustomerSuggestionRepository | None = None,
    ) -> None:
        self._connection = connection
        self._projects = projects or SqliteCustomerProjectRepository(connection)
        self._suggestions = suggestions or SqliteCustomerSuggestionRepository(connection)
        self._mapper = SqliteCustomerMapper(connection, self._projects)
        self._idempotency = SqliteIdempotencyRepository(connection)

    def list(self, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT id FROM customers ORDER BY display_name COLLATE NOCASE, id "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [customer for row in rows if (customer := self.get(int(row[0]))) is not None]

    def get(self, customer_id: int) -> dict[str, Any] | None:
        return self._mapper.aggregate(customer_id)

    def find_by_display_name(self, value: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT id FROM customers WHERE display_name=? COLLATE NOCASE ORDER BY id LIMIT 1",
            (value.strip(),),
        ).fetchone()
        return self.get(int(row[0])) if row is not None else None

    def create(self, customer: dict[str, Any]) -> dict[str, Any]:
        values = self._normalized_customer(customer)
        folder_path = str(values.get("folder_path", "")).strip()
        if not folder_path:
            folder_path = f"customer://{uuid.uuid4().hex}"
        cursor = self._connection.execute(
            """
            INSERT INTO customers
                (folder_path, display_name, entity_type, company, email, phone,
                 street, postal_code, city, revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                folder_path,
                *(values[field] for field in CUSTOMER_FIELDS),
            ),
        )
        customer_id = int(cursor.lastrowid)
        self._replace_children(customer_id, values, folder_path)
        created = self.get(customer_id)
        assert created is not None
        return created

    def update(
        self, customer_id: int, customer: dict[str, Any], expected_revision: int
    ) -> dict[str, Any]:
        current = self.get(customer_id)
        if current is None or current["revision"] != expected_revision:
            raise CustomerConflictError(current)
        merged = dict(current)
        merged.update(customer)
        values = self._normalized_customer(merged)
        folder_path = str(values.get("folder_path") or current["folder_path"])
        cursor = self._connection.execute(
            """
            UPDATE customers SET
                folder_path=?, display_name=?, entity_type=?, company=?, email=?,
                phone=?, street=?, postal_code=?, city=?, revision=revision+1,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND revision=?
            """,
            (
                folder_path,
                *(values[field] for field in CUSTOMER_FIELDS),
                customer_id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise CustomerConflictError(self.get(customer_id))
        self._replace_children(customer_id, values, folder_path)
        saved = self.get(customer_id)
        assert saved is not None
        return saved

    def delete(self, customer_id: int, expected_revision: int) -> None:
        current = self.get(customer_id)
        if current is None or current["revision"] != expected_revision:
            raise CustomerConflictError(current)
        cursor = self._connection.execute(
            "DELETE FROM customers WHERE id=? AND revision=?",
            (customer_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise CustomerConflictError(self.get(customer_id))

    def add_journal(
        self, customer_id: int, entry: dict[str, Any], expected_revision: int
    ) -> tuple[dict[str, Any], int]:
        self._require_revision(customer_id, expected_revision)
        entry_number = int(
            self._connection.execute(
                "SELECT COALESCE(MAX(entry_number), 0)+1 "
                "FROM customer_journal_entries WHERE customer_id=?",
                (customer_id,),
            ).fetchone()[0]
        )
        cursor = self._connection.execute(
            """
            INSERT INTO customer_journal_entries
                (customer_id, entry_number, title, body)
            VALUES (?, ?, ?, ?)
            """,
            (
                customer_id,
                entry_number,
                str(entry.get("title", "")).strip(),
                str(entry.get("body", "")).strip(),
            ),
        )
        revision = self._increment_revision(customer_id, expected_revision)
        return self._journal(int(cursor.lastrowid)), revision

    def update_journal(
        self,
        customer_id: int,
        entry_id: int,
        entry: dict[str, Any],
        expected_revision: int,
    ) -> tuple[dict[str, Any], int]:
        self._require_revision(customer_id, expected_revision)
        cursor = self._connection.execute(
            """
            UPDATE customer_journal_entries SET title=?, body=?,
                   updated_at=CURRENT_TIMESTAMP
             WHERE id=? AND customer_id=?
            """,
            (
                str(entry.get("title", "")).strip(),
                str(entry.get("body", "")).strip(),
                entry_id,
                customer_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ResourceNotFoundError("Journaleintrag nicht gefunden.")
        revision = self._increment_revision(customer_id, expected_revision)
        return self._journal(entry_id), revision

    def delete_journal(
        self, customer_id: int, entry_id: int, expected_revision: int
    ) -> int:
        self._require_revision(customer_id, expected_revision)
        cursor = self._connection.execute(
            "DELETE FROM customer_journal_entries WHERE id=? AND customer_id=?",
            (entry_id, customer_id),
        )
        if cursor.rowcount != 1:
            raise ResourceNotFoundError("Journaleintrag nicht gefunden.")
        return self._increment_revision(customer_id, expected_revision)

    def idempotency_result(
        self, key: str, request_hash: str
    ) -> CustomerMutationResult | None:
        return self._idempotency.result(key, request_hash)

    def remember_idempotency(
        self,
        key: str,
        request_hash: str,
        operation: str,
        result: CustomerMutationResult,
    ) -> None:
        self._idempotency.remember(key, request_hash, operation, result)

    def add_document_suggestion(
        self,
        customer_id: int,
        *,
        kind: str,
        value: str,
        source_path: str,
        excerpt: str,
        fingerprint: str,
        confidence: float,
    ) -> bool:
        """Compatibility facade over the focused suggestion repository."""
        return self._suggestions.add(
            customer_id,
            kind=kind,
            value=value,
            source_path=source_path,
            excerpt=excerpt,
            fingerprint=fingerprint,
            confidence=confidence,
        )

    def _replace_children(
        self, customer_id: int, customer: dict[str, Any], primary_folder: str
    ) -> None:
        self._connection.execute("DELETE FROM contacts WHERE customer_id=?", (customer_id,))
        self._connection.executemany(
            "INSERT INTO contacts (customer_id, name, role, email, phone) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    customer_id,
                    str(item.get("name", "")).strip(),
                    str(item.get("role", "")).strip(),
                    str(item.get("email", "")).strip(),
                    str(item.get("phone", "")).strip(),
                )
                for item in customer.get("contacts", [])
                if isinstance(item, dict)
            ],
        )
        self._replace_values(
            "customer_services", "name", customer_id, customer.get("service_types", [])
        )
        folders = customer.get("folder_paths") or [primary_folder]
        self._replace_values("customer_folders", "folder_path", customer_id, folders)
        # Portable projects are server-derived. A stale/manual client payload must
        # never remove their compatibility folder/service links.
        self._connection.execute(
            "INSERT OR IGNORE INTO customer_folders(customer_id, folder_path) "
            "SELECT customer_id, folder_path FROM customer_projects WHERE customer_id=?",
            (customer_id,),
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO customer_services(customer_id, name) "
            "SELECT customer_id, service_type FROM customer_projects WHERE customer_id=?",
            (customer_id,),
        )
        self._replace_values("notes", "body", customer_id, customer.get("notes", []))
        self._connection.execute("DELETE FROM customer_tags WHERE customer_id=?", (customer_id,))
        for value in unique_strings(customer.get("tags", [])):
            self._connection.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (value,))
            self._connection.execute(
                "INSERT OR IGNORE INTO customer_tags(customer_id, tag_id) "
                "SELECT ?, id FROM tags WHERE name=? COLLATE NOCASE",
                (customer_id, value),
            )

    def _replace_values(
        self, table: str, column: str, customer_id: int, values: Iterable[object]
    ) -> None:
        # Table/column names are constants controlled by this module.
        self._connection.execute(f"DELETE FROM {table} WHERE customer_id=?", (customer_id,))
        self._connection.executemany(
            f"INSERT INTO {table}(customer_id, {column}) VALUES (?, ?)",
            [(customer_id, value) for value in unique_strings(values)],
        )

    @staticmethod
    def _normalized_customer(customer: dict[str, Any]) -> dict[str, Any]:
        return normalized_customer(customer)

    def _journal(self, entry_id: int) -> dict[str, Any]:
        return self._mapper.journal(entry_id)

    def _require_revision(self, customer_id: int, expected: int) -> None:
        current = self.get(customer_id)
        if current is None or int(current["revision"]) != expected:
            raise CustomerConflictError(current)

    def _increment_revision(self, customer_id: int, expected: int) -> int:
        cursor = self._connection.execute(
            "UPDATE customers SET revision=revision+1, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND revision=?",
            (customer_id, expected),
        )
        if cursor.rowcount != 1:
            raise CustomerConflictError(self.get(customer_id))
        return expected + 1
