"""Focused read queries for catalog documents and their facets."""

from __future__ import annotations

from contextlib import closing
from typing import Callable

from papagui_contracts import CatalogFacets

from papagui_client.application.models import (
    CatalogQuery,
    CatalogRecord,
    GlobalSearchKind,
    GlobalSearchQuery,
    GlobalSearchRecord,
)

from .sqlite_catalog_mapping import record
from .sqlite_catalog_schema import (
    columns,
    read_only,
    require_portable_files,
    selected_file_columns,
    tables,
)


class CatalogDocumentQueries:
    def __init__(self, database: Callable):
        self._database = database

    def search(self, query: CatalogQuery) -> list[CatalogRecord]:
        with closing(read_only(self._database())) as connection:
            file_columns = columns(connection, "files")
            all_tables = tables(connection)
            require_portable_files(file_columns)
            clauses: list[str] = []
            values: list[object] = []
            self._append_text(
                clauses, values, query.text, file_columns, all_tables
            )
            for column, value in (
                ("source_id", query.source_id),
                ("file_type", query.file_type),
                ("customer_name", query.customer_name),
                ("year", query.year),
            ):
                if value is not None:
                    if column not in file_columns:
                        return []
                    clauses.append(f"{column}=?")
                    values.append(value)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            modified = "modified_date" if "modified_date" in file_columns else "''"
            rows = connection.execute(
                f"SELECT {selected_file_columns(file_columns)} FROM files {where} "
                f"ORDER BY {modified} DESC,filename COLLATE NOCASE LIMIT ? OFFSET ?",
                (*values, query.limit, query.offset),
            ).fetchall()
        return [record(row) for row in rows]

    def get(self, document_key: str) -> CatalogRecord | None:
        with closing(read_only(self._database())) as connection:
            file_columns = columns(connection, "files")
            require_portable_files(file_columns)
            row = connection.execute(
                f"SELECT {selected_file_columns(file_columns)} FROM files "
                "WHERE document_key=?",
                (document_key,),
            ).fetchone()
        return record(row) if row is not None else None

    def facets(self, source_id: str | None = None) -> CatalogFacets:
        with closing(read_only(self._database())) as connection:
            file_columns = columns(connection, "files")
            require_portable_files(file_columns)
            values: dict[str, tuple[str, ...]] = {}
            for key, candidates in (
                ("domains", ("domain_folder",)),
                ("years", ("time_bucket", "year")),
                ("file_types", ("file_type",)),
            ):
                column = next(
                    (name for name in candidates if name in file_columns), None
                )
                if column is None:
                    values[key] = ()
                    continue
                clauses = [f"{column}<>''"]
                parameters: list[object] = []
                if source_id is not None:
                    clauses.append("source_id=?")
                    parameters.append(source_id)
                rows = connection.execute(
                    f"SELECT DISTINCT {column} FROM files "
                    f"WHERE {' AND '.join(clauses)} ORDER BY {column} COLLATE NOCASE",
                    parameters,
                ).fetchall()
                values[key] = tuple(str(row[0]) for row in rows)
        values["years"] = tuple(
            sorted(
                values["years"],
                key=lambda value: (value.isdigit(), value.casefold()),
                reverse=True,
            )
        )
        return CatalogFacets(**values)

    def global_records(self, query: GlobalSearchQuery) -> list[GlobalSearchRecord]:
        with closing(read_only(self._database())) as connection:
            file_columns = columns(connection, "files")
            all_tables = tables(connection)
            require_portable_files(file_columns)
            clauses: list[str] = []
            values: list[object] = []
            self._append_text(
                clauses, values, query.text, file_columns, all_tables
            )
            filters = (
                ("source_id", query.source_id),
                ("domain_folder", query.domain_folder),
                ("file_type", query.file_type),
                (
                    "time_bucket" if "time_bucket" in file_columns else "year",
                    query.year,
                ),
                ("customer_name", query.customer_name),
            )
            for column, value in filters:
                if value is not None:
                    if column not in file_columns:
                        return []
                    clauses.append(f"{column}=?")
                    values.append(value.lstrip(".") if column == "file_type" else value)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = connection.execute(
                f"SELECT {selected_file_columns(file_columns)} FROM files {where}",
                values,
            ).fetchall()
        result = []
        for row in rows:
            value = record(row)
            result.append(
                GlobalSearchRecord(
                    GlobalSearchKind.DOCUMENT,
                    value.document_key,
                    value.filename,
                    " · ".join(
                        item
                        for item in (value.customer_name, value.project_name, value.year)
                        if item
                    ),
                    value.source_id,
                    value.relative_path,
                    value.modified_date,
                    metadata={"catalog_record": value},
                )
            )
        return result

    @staticmethod
    def _append_text(
        clauses: list[str],
        values: list[object],
        text: str,
        file_columns: set[str],
        all_tables: set[str],
    ) -> None:
        text = text.strip()
        if not text:
            return
        searchable = [
            name
            for name in (
                "filename",
                "customer_name",
                "project_name",
                "relative_path",
            )
            if name in file_columns
        ]
        expressions = [f"{name} LIKE ?" for name in searchable]
        values.extend([f"%{text}%"] * len(searchable))
        if "file_content_fts" in all_tables and "path" in file_columns:
            # Evaluate the FTS query once and use its paths as a materialized
            # list.  A correlated EXISTS makes SQLite execute the virtual FTS
            # scan once per catalog row; on real customer catalogs that can
            # block the Qt event loop for tens of seconds.
            expressions.append(
                "path IN (SELECT path FROM file_content_fts "
                "WHERE file_content_fts MATCH ?)"
            )
            values.append(f'"{text.replace(chr(34), chr(34) * 2)}"')
        clauses.append("(" + " OR ".join(expressions) + ")")
