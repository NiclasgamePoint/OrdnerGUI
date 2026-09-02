"""Focused portable folder and project-root queries."""

from __future__ import annotations

from contextlib import closing
from typing import Callable

from papagui_contracts import CatalogFolder, CatalogProjectRoot

from papagui_client.application.models import (
    CatalogRecord,
    GlobalSearchKind,
    GlobalSearchQuery,
    GlobalSearchRecord,
)

from .sqlite_catalog_mapping import folder as map_folder
from .sqlite_catalog_mapping import project as map_project
from .sqlite_catalog_mapping import record
from .sqlite_catalog_schema import (
    CatalogUnavailableError,
    columns,
    read_only,
    require_portable_files,
    selected_file_columns,
    tables,
)


class CatalogNavigationQueries:
    def __init__(self, database: Callable):
        self._database = database

    def folders(
        self,
        source_id: str | None = None,
        *,
        query: str = "",
        project_only: bool = False,
    ) -> list[CatalogFolder]:
        with closing(read_only(self._database())) as connection:
            if "folders" not in tables(connection):
                return []
            folder_columns = columns(connection, "folders")
            if not {"id", "source_id", "relative_path", "name"} <= folder_columns:
                raise CatalogUnavailableError("Catalog folder records are not portable v2")
            file_columns = columns(connection, "files")
            count = (
                "(SELECT COUNT(*) FROM files item WHERE item.folder_id=folders.id)"
                if "folder_id" in file_columns
                else "0"
            )
            size = (
                "(SELECT COALESCE(SUM(item.file_size),0) FROM files item "
                "WHERE item.folder_id=folders.id)"
                if {"folder_id", "file_size"} <= file_columns
                else "0"
            )
            modified = (
                "(SELECT MAX(item.modified_date) FROM files item "
                "WHERE item.folder_id=folders.id)"
                if {"folder_id", "modified_date"} <= file_columns
                else "NULL"
            )
            clauses: list[str] = []
            parameters: list[object] = []
            if source_id is not None:
                clauses.append("folders.source_id=?")
                parameters.append(source_id)
            if query.strip():
                clauses.append("(folders.name LIKE ? OR folders.relative_path LIKE ?)")
                term = f"%{query.strip()}%"
                parameters.extend((term, term))
            if project_only:
                if "project_root_id" not in folder_columns:
                    return []
                clauses.append("folders.project_root_id IS NOT NULL")
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            parent = "parent_id" if "parent_id" in folder_columns else "NULL"
            project = (
                "project_root_id" if "project_root_id" in folder_columns else "NULL"
            )
            rows = connection.execute(
                f"SELECT id,source_id,relative_path,name,{parent} AS parent_id,"
                f"{project} AS project_root_id,{count} AS file_count,"
                f"{size} AS total_size,{modified} AS last_modified "
                f"FROM folders {where} ORDER BY relative_path COLLATE NOCASE",
                parameters,
            ).fetchall()
        return [map_folder(row) for row in rows]

    def folder(
        self, source_id: str, relative_path: str
    ) -> tuple[CatalogFolder, list[CatalogFolder], list[CatalogRecord]]:
        values = self.folders(source_id)
        selected = next(
            (item for item in values if item.source.relative_path == relative_path),
            None,
        )
        if selected is None:
            raise CatalogUnavailableError("Catalog folder not found")
        children = [item for item in values if item.parent_id == selected.id]
        with closing(read_only(self._database())) as connection:
            file_columns = columns(connection, "files")
            require_portable_files(file_columns)
            if "folder_id" in file_columns:
                clause, parameter = "folder_id=?", selected.id
            elif "relative_dir" in file_columns:
                clause, parameter = "relative_dir=?", selected.source.relative_path
            else:
                clause, parameter = (
                    "relative_path LIKE ?",
                    relative_path.rstrip("/") + "/%",
                )
            rows = connection.execute(
                f"SELECT {selected_file_columns(file_columns)} FROM files WHERE source_id=? "
                f"AND {clause} ORDER BY filename COLLATE NOCASE",
                (source_id, parameter),
            ).fetchall()
        return selected, children, [record(row) for row in rows]

    def project_roots(self, source_id: str | None = None) -> list[CatalogProjectRoot]:
        with closing(read_only(self._database())) as connection:
            if "project_roots" not in tables(connection):
                return []
            project_columns = columns(connection, "project_roots")
            required = {
                "id",
                "source_id",
                "relative_path",
                "service_type",
                "year",
                "customer_label",
                "customer_name",
            }
            if not required <= project_columns:
                raise CatalogUnavailableError("Catalog project roots are not portable v2")
            where = "WHERE source_id=?" if source_id is not None else ""
            parameters = (source_id,) if source_id is not None else ()
            city = "city" if "city" in project_columns else "''"
            recognition = (
                "recognition_key" if "recognition_key" in project_columns else "''"
            )
            rows = connection.execute(
                "SELECT id,source_id,relative_path,service_type,year,customer_label,"
                f"customer_name,{city} AS city,{recognition} AS recognition_key "
                f"FROM project_roots {where} "
                "ORDER BY customer_name COLLATE NOCASE,year DESC,relative_path COLLATE NOCASE",
                parameters,
            ).fetchall()
        return [map_project(row) for row in rows]

    def folder_records(self, query: GlobalSearchQuery) -> list[GlobalSearchRecord]:
        term = query.text.strip().casefold()
        result = []
        for value in self.folders(query.source_id, query=query.text):
            if query.year and query.year not in value.source.relative_path.split("/"):
                continue
            if (
                query.domain_folder
                and query.domain_folder.casefold()
                not in value.source.relative_path.casefold()
            ):
                continue
            if (
                term
                and term not in value.name.casefold()
                and term not in value.source.relative_path.casefold()
            ):
                continue
            result.append(
                GlobalSearchRecord(
                    GlobalSearchKind.FOLDER,
                    f"folder:{value.source.source_id}:{value.id}",
                    value.name,
                    f"{value.file_count} Dateien",
                    value.source.source_id,
                    value.source.relative_path,
                    value.last_modified or "",
                    metadata={"folder": value},
                )
            )
        return result

    def project_records(self, query: GlobalSearchQuery) -> list[GlobalSearchRecord]:
        term = query.text.strip().casefold()
        result = []
        for value in self.project_roots(query.source_id):
            searchable = " ".join(
                (
                    value.customer_name,
                    value.customer_label,
                    value.service_type,
                    value.city,
                    value.source.relative_path,
                )
            ).casefold()
            if term and term not in searchable:
                continue
            if query.year and str(value.year) != query.year:
                continue
            if (
                query.customer_name
                and query.customer_name.casefold() not in value.customer_name.casefold()
            ):
                continue
            if (
                query.domain_folder
                and value.service_type.casefold() != query.domain_folder.casefold()
            ):
                continue
            result.append(
                GlobalSearchRecord(
                    GlobalSearchKind.PROJECT,
                    f"project:{value.source.source_id}:{value.id}",
                    value.customer_name,
                    f"{value.service_type} · {value.year} · {value.city}".strip(" ·"),
                    value.source.source_id,
                    value.source.relative_path,
                    metadata={"project": value},
                )
            )
        return result
