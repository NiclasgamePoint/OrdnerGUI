"""SQLite row mapping and input normalization for customer aggregates."""

from __future__ import annotations

from collections.abc import Iterable
import sqlite3
from typing import Any

from papagui_server.adapters.customer_projects import SqliteCustomerProjectRepository
from papagui_server.domain.errors import ResourceNotFoundError


CUSTOMER_FIELDS = (
    "display_name",
    "entity_type",
    "company",
    "email",
    "phone",
    "street",
    "postal_code",
    "city",
)


def normalized_customer(customer: dict[str, Any]) -> dict[str, Any]:
    display_name = str(customer.get("display_name", "")).strip()
    if not display_name:
        raise ValueError("Der Kundenname darf nicht leer sein.")
    result = dict(customer)
    result["display_name"] = display_name
    result["entity_type"] = str(customer.get("entity_type") or "Unternehmen").strip()
    for field in CUSTOMER_FIELDS[2:]:
        result[field] = str(customer.get(field, "")).strip()
    return result


def unique_strings(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


class SqliteCustomerMapper:
    def __init__(
        self,
        connection: sqlite3.Connection,
        projects: SqliteCustomerProjectRepository,
    ) -> None:
        self._connection = connection
        self._projects = projects

    def aggregate(self, customer_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT id, folder_path, display_name, entity_type, company, email,
                   phone, street, postal_code, city, revision
              FROM customers WHERE id=?
            """,
            (customer_id,),
        ).fetchone()
        if row is None:
            return None
        folders = self.values(
            "SELECT folder_path FROM customer_folders WHERE customer_id=? "
            "ORDER BY rowid",
            customer_id,
        )
        contacts = [
            {
                "name": str(item[0]),
                "role": str(item[1]),
                "email": str(item[2]),
                "phone": str(item[3]),
            }
            for item in self._connection.execute(
                "SELECT name, role, email, phone FROM contacts "
                "WHERE customer_id=? ORDER BY id",
                (customer_id,),
            )
        ]
        journals = [
            {
                "id": int(item[0]),
                "customer_id": customer_id,
                "entry_number": int(item[1]),
                "title": str(item[2]),
                "body": str(item[3]),
                "created_at": str(item[4]),
                "updated_at": str(item[5]),
            }
            for item in self._connection.execute(
                """
                SELECT id, entry_number, title, body, created_at, updated_at
                  FROM customer_journal_entries
                 WHERE customer_id=? ORDER BY entry_number, id
                """,
                (customer_id,),
            )
        ]
        return {
            "id": int(row["id"]),
            "revision": int(row["revision"]),
            "folder_path": str(row["folder_path"]),
            "folder_paths": folders or [str(row["folder_path"])],
            "display_name": str(row["display_name"]),
            "entity_type": str(row["entity_type"]),
            "service_types": self.values(
                "SELECT name FROM customer_services WHERE customer_id=? "
                "ORDER BY name COLLATE NOCASE",
                customer_id,
            ),
            "company": str(row["company"]),
            "email": str(row["email"]),
            "phone": str(row["phone"]),
            "street": str(row["street"]),
            "postal_code": str(row["postal_code"]),
            "city": str(row["city"]),
            "contacts": contacts,
            "notes": self.values(
                "SELECT body FROM notes WHERE customer_id=? ORDER BY id", customer_id
            ),
            "tags": self.values(
                """
                SELECT tags.name FROM tags
                JOIN customer_tags ON customer_tags.tag_id=tags.id
                WHERE customer_tags.customer_id=? ORDER BY tags.name COLLATE NOCASE
                """,
                customer_id,
            ),
            "journal_entries": journals,
            "projects": self._projects.list_for_customer(customer_id),
        }

    def journal(self, entry_id: int) -> dict[str, Any]:
        row = self._connection.execute(
            """
            SELECT id, customer_id, entry_number, title, body, created_at, updated_at
              FROM customer_journal_entries WHERE id=?
            """,
            (entry_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Journaleintrag nicht gefunden.")
        return dict(row)

    def values(self, query: str, customer_id: int) -> list[str]:
        return [str(row[0]) for row in self._connection.execute(query, (customer_id,))]
