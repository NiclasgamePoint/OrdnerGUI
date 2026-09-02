"""Focused persistence adapter for portable customer/project assignments."""

from __future__ import annotations

import sqlite3
from typing import Any

from papagui_contracts import CatalogProjectRoot, CustomerProject, SourcePath

from papagui_server.domain.folder_structure import normalize_identity
from papagui_server.domain.source_paths import source_uri
from papagui_server.domain.errors import ProjectAssignmentConflictError


class SqliteCustomerProjectRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_for_customer(self, customer_id: int) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT id, customer_id, project_root_id, source_id, relative_path,
                   service_type, project_label, project_city, year, provenance
              FROM customer_projects
             WHERE customer_id=?
             ORDER BY year DESC, service_type COLLATE NOCASE,
                      relative_path COLLATE NOCASE
            """,
            (customer_id,),
        ).fetchall()
        return [self._map(row).to_dict() for row in rows]

    def customer_id_for_source(self, source: SourcePath) -> int | None:
        row = self._connection.execute(
            "SELECT customer_id FROM customer_projects "
            "WHERE source_id=? AND relative_path=?",
            (source.source_id, source.relative_path),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def upsert(
        self,
        root_payload: dict[str, Any],
        *,
        customer_id: int,
    ) -> dict[str, Any]:
        root = CatalogProjectRoot.from_dict(root_payload)
        existing = self._connection.execute(
            "SELECT id, customer_id FROM customer_projects "
            "WHERE source_id=? AND relative_path=?",
            (root.source.source_id, root.source.relative_path),
        ).fetchone()
        if existing is not None and int(existing["customer_id"]) != customer_id:
            raise ProjectAssignmentConflictError(int(existing["customer_id"]))
        folder = source_uri(root.source)
        service_type_id = self._service_type_id(root.service_type)
        self._connection.execute(
            """
            INSERT INTO customer_projects(
                customer_id, project_root_id, source_id, relative_path,
                service_type, service_type_id, folder_path, folder_key,
                project_label, project_city, year, source, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'folder', 'folder')
            ON CONFLICT(source_id, relative_path) DO UPDATE SET
                project_root_id=excluded.project_root_id,
                service_type=excluded.service_type,
                service_type_id=excluded.service_type_id,
                folder_path=excluded.folder_path,
                folder_key=excluded.folder_key,
                project_label=excluded.project_label,
                project_city=excluded.project_city,
                year=excluded.year,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                customer_id,
                root.id,
                root.source.source_id,
                root.source.relative_path,
                root.service_type,
                service_type_id,
                folder,
                folder.casefold(),
                root.customer_label,
                root.city,
                root.year,
            ),
        )
        folder_added = self._connection.execute(
            "INSERT OR IGNORE INTO customer_folders(customer_id, folder_path) "
            "VALUES (?, ?)",
            (customer_id, folder),
        ).rowcount
        service_added = self._connection.execute(
            "INSERT OR IGNORE INTO customer_services(customer_id, name) VALUES (?, ?)",
            (customer_id, root.service_type),
        ).rowcount
        if existing is None or folder_added or service_added:
            self._connection.execute(
                "UPDATE customers SET revision=revision+1, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (customer_id,),
            )
        row = self._connection.execute(
            """
            SELECT id, customer_id, project_root_id, source_id, relative_path,
                   service_type, project_label, project_city, year, provenance
              FROM customer_projects
             WHERE source_id=? AND relative_path=?
            """,
            (root.source.source_id, root.source.relative_path),
        ).fetchone()
        assert row is not None
        return self._map(row).to_dict()

    def _service_type_id(self, name: str) -> int:
        normalized = normalize_identity(name) or "unbekannt"
        self._connection.execute(
            "INSERT INTO service_types(name, normalized_name) VALUES (?, ?) "
            "ON CONFLICT(normalized_name) DO UPDATE SET name=excluded.name",
            (name or "Unbekannt", normalized),
        )
        row = self._connection.execute(
            "SELECT id FROM service_types WHERE normalized_name=?", (normalized,)
        ).fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _map(row: sqlite3.Row) -> CustomerProject:
        return CustomerProject(
            id=int(row["id"]),
            customer_id=int(row["customer_id"]),
            project_root_id=(
                int(row["project_root_id"])
                if row["project_root_id"] is not None
                else None
            ),
            source=SourcePath(str(row["source_id"]), str(row["relative_path"])),
            service_type=str(row["service_type"]),
            project_label=str(row["project_label"]),
            project_city=str(row["project_city"]),
            year=int(row["year"]) if row["year"] is not None else None,
            provenance=str(row["provenance"]),
        )
