"""Read-only portable catalog queries for API and snapshot consumers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
from typing import Any

from papagui_contracts import (
    CatalogFacets,
    CatalogFile,
    CatalogFolder,
    CatalogProjectRoot,
    SourcePath,
)

from papagui_server.domain.errors import ResourceNotFoundError


class SqliteCatalogReader:
    """Expose searches without any writer or host-path capability."""

    _SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    _SORTS = {
        "name": "files.filename COLLATE NOCASE ASC, files.relative_path ASC",
        "modified": "files.modified_date DESC, files.relative_path ASC",
        "size": "files.file_size DESC, files.relative_path ASC",
    }

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def search(
        self,
        *,
        source_id: str,
        query: str = "",
        domain_folder: str = "",
        time_bucket: str = "",
        file_type: str = "",
        project_root_id: int | None = None,
        sort: str = "name",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._validate_page(source_id, sort, limit, offset)
        clauses = ["files.source_id=?"]
        parameters: list[Any] = [source_id]
        if domain_folder:
            clauses.append("files.domain_folder=? COLLATE NOCASE")
            parameters.append(domain_folder)
        if time_bucket:
            clauses.append("files.time_bucket=? COLLATE NOCASE")
            parameters.append(time_bucket)
        if file_type:
            clauses.append("files.file_type=? COLLATE NOCASE")
            parameters.append(file_type.lstrip("."))
        if project_root_id is not None:
            if isinstance(project_root_id, bool) or project_root_id < 1:
                raise ValueError("project_root_id ist ungültig.")
            clauses.append("files.project_root_id=?")
            parameters.append(project_root_id)
        words = re.findall(r"\w+", query, flags=re.UNICODE)
        if words:
            like = f"%{' '.join(words)}%"
            fts = " AND ".join(f'"{word.replace(chr(34), "")}"*' for word in words)
            clauses.append(
                "(files.filename LIKE ? OR files.relative_path LIKE ? OR EXISTS ("
                "SELECT 1 FROM file_content_fts content "
                "WHERE content.path=files.path AND file_content_fts MATCH ?))"
            )
            parameters.extend((like, like, fts))
        where = " AND ".join(clauses)
        with self._connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM files WHERE {where}", parameters
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT id, source_id, relative_path, filename, file_type,
                       file_size, modified_date, domain_folder, time_bucket,
                       project_name, relative_dir, folder_id, project_root_id
                  FROM files
                 WHERE {where}
                 ORDER BY {self._SORTS[sort]}
                 LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
        return {
            "items": [self._file(row).to_dict() for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def facets(self, *, source_id: str) -> dict[str, Any]:
        self._validate_source_id(source_id)
        values: dict[str, tuple[str, ...]] = {}
        with self._connection() as connection:
            for key, column in (
                ("domains", "domain_folder"),
                ("years", "time_bucket"),
                ("file_types", "file_type"),
            ):
                rows = connection.execute(
                    f"SELECT DISTINCT {column} FROM files WHERE source_id=? "
                    f"AND {column}<>'' ORDER BY {column} COLLATE NOCASE",
                    (source_id,),
                )
                values[key] = tuple(str(row[0]) for row in rows)
        values["years"] = tuple(
            sorted(values["years"], key=lambda value: (value.isdigit(), value), reverse=True)
        )
        return CatalogFacets(**values).to_dict()

    def list_folders(
        self,
        *,
        source_id: str,
        project_only: bool = False,
        query: str = "",
    ) -> list[dict[str, Any]]:
        self._validate_source_id(source_id)
        clauses = ["folders.source_id=?"]
        parameters: list[Any] = [source_id]
        if project_only:
            clauses.append("folders.project_root_id IS NOT NULL")
            clauses.append("folders.relative_path=project_roots.relative_path")
        if query.strip():
            clauses.append("(folders.name LIKE ? OR folders.relative_path LIKE ?)")
            parameters.extend((f"%{query.strip()}%", f"%{query.strip()}%"))
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT folders.id, folders.source_id, folders.relative_path,
                       folders.name, folders.parent_id, folders.project_root_id,
                       COUNT(files.id) AS file_count,
                       COALESCE(SUM(files.file_size), 0) AS total_size,
                       MAX(files.modified_date) AS last_modified
                  FROM folders
                  LEFT JOIN project_roots ON project_roots.id=folders.project_root_id
                  LEFT JOIN files ON files.folder_id=folders.id
                 WHERE {' AND '.join(clauses)}
                 GROUP BY folders.id
                 ORDER BY folders.relative_path COLLATE NOCASE
                """,
                parameters,
            ).fetchall()
        return [self._folder(row).to_dict() for row in rows]

    def folder_details(self, *, source_id: str, relative_path: str) -> dict[str, Any]:
        source = SourcePath(source_id, relative_path)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id, source_id, relative_path, name, parent_id, project_root_id "
                "FROM folders WHERE source_id=? AND relative_path=?",
                (source.source_id, source.relative_path),
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("Ordner nicht gefunden.")
            pattern = source.relative_path + "/%"
            stats = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(file_size), 0), MAX(modified_date) "
                "FROM files WHERE source_id=? AND "
                "(relative_dir=? OR relative_dir LIKE ?)",
                (source.source_id, source.relative_path, pattern),
            ).fetchone()
            children = connection.execute(
                "SELECT id, source_id, relative_path, name, parent_id, "
                "project_root_id, 0 AS file_count, 0 AS total_size, "
                "NULL AS last_modified FROM folders WHERE parent_id=? "
                "ORDER BY name COLLATE NOCASE",
                (int(row["id"]),),
            ).fetchall()
        folder = CatalogFolder(
            id=int(row["id"]),
            source=source,
            name=str(row["name"]),
            parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
            project_root_id=(int(row["project_root_id"]) if row["project_root_id"] is not None else None),
            file_count=int(stats[0]),
            total_size=int(stats[1]),
            last_modified=str(stats[2]) if stats[2] is not None else None,
        )
        return {
            "folder": folder.to_dict(),
            "children": [self._folder(item).to_dict() for item in children],
        }

    def list_project_roots(self, *, source_id: str) -> list[dict[str, Any]]:
        self._validate_source_id(source_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM project_roots WHERE source_id=? "
                "ORDER BY recognition_key, year DESC, relative_path COLLATE NOCASE",
                (source_id,),
            ).fetchall()
        return [self._project(row).to_dict() for row in rows]

    def project_root(self, *, source_id: str, project_root_id: int) -> dict[str, Any]:
        self._validate_source_id(source_id)
        if isinstance(project_root_id, bool) or project_root_id < 1:
            raise ValueError("project_root_id ist ungültig.")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM project_roots WHERE source_id=? AND id=?",
                (source_id, project_root_id),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Projektwurzel nicht gefunden.")
        return self._project(row).to_dict()

    def document_evidence(
        self, *, source_id: str, documents_per_project: int = 24
    ) -> list[dict[str, Any]]:
        """Read bounded extracted text for recognition without exposing host paths."""
        self._validate_source_id(source_id)
        if (
            isinstance(documents_per_project, bool)
            or not 1 <= documents_per_project <= 500
        ):
            raise ValueError("Ungültiges Dokumentlimit.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT files.project_root_id, files.source_id, files.relative_path,
                       files.modified_date, file_content_fts.content
                  FROM files
                  JOIN file_content_fts ON file_content_fts.path=files.path
                 WHERE files.source_id=? AND files.project_root_id IS NOT NULL
                 ORDER BY files.project_root_id, files.modified_date DESC,
                          files.relative_path COLLATE NOCASE
                """,
                (source_id,),
            ).fetchall()
        counts: dict[int, int] = {}
        result: list[dict[str, Any]] = []
        for row in rows:
            project_root_id = int(row["project_root_id"])
            if counts.get(project_root_id, 0) >= documents_per_project:
                continue
            counts[project_root_id] = counts.get(project_root_id, 0) + 1
            result.append(
                {
                    "project_root_id": project_root_id,
                    "source": SourcePath(
                        str(row["source_id"]), str(row["relative_path"])
                    ).to_dict(),
                    "content": str(row["content"]),
                    "modified_at": str(row["modified_date"]),
                }
            )
        return result

    @contextmanager
    def _connection(self):
        if not self.database_path.is_file():
            raise ResourceNotFoundError("Es ist noch kein Katalog vorhanden.")
        connection = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @classmethod
    def _validate_source_id(cls, source_id: str) -> None:
        if not isinstance(source_id, str) or not cls._SOURCE_ID.fullmatch(source_id):
            raise ValueError("source_id ist ungültig.")

    @classmethod
    def _validate_page(cls, source_id: str, sort: str, limit: int, offset: int) -> None:
        cls._validate_source_id(source_id)
        if sort not in cls._SORTS:
            raise ValueError("Unbekannte Sortierung.")
        if isinstance(limit, bool) or not 1 <= limit <= 2_000 or offset < 0:
            raise ValueError("Ungültige Seiteneinteilung.")

    @staticmethod
    def _file(row: sqlite3.Row) -> CatalogFile:
        return CatalogFile(
            id=int(row["id"]),
            source=SourcePath(str(row["source_id"]), str(row["relative_path"])),
            filename=str(row["filename"]),
            file_type=str(row["file_type"]),
            file_size=int(row["file_size"]),
            modified_at=str(row["modified_date"]),
            domain_folder=str(row["domain_folder"]),
            time_bucket=str(row["time_bucket"]),
            project_name=str(row["project_name"]),
            relative_dir=str(row["relative_dir"]),
            folder_id=int(row["folder_id"]) if row["folder_id"] is not None else None,
            project_root_id=(int(row["project_root_id"]) if row["project_root_id"] is not None else None),
        )

    @staticmethod
    def _folder(row: sqlite3.Row) -> CatalogFolder:
        return CatalogFolder(
            id=int(row["id"]),
            source=SourcePath(str(row["source_id"]), str(row["relative_path"])),
            name=str(row["name"]),
            parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
            project_root_id=(int(row["project_root_id"]) if row["project_root_id"] is not None else None),
            file_count=int(row["file_count"]),
            total_size=int(row["total_size"]),
            last_modified=str(row["last_modified"]) if row["last_modified"] is not None else None,
        )

    @staticmethod
    def _project(row: sqlite3.Row) -> CatalogProjectRoot:
        return CatalogProjectRoot(
            id=int(row["id"]),
            source=SourcePath(str(row["source_id"]), str(row["relative_path"])),
            service_type=str(row["service_type"]),
            year=int(row["year"]),
            customer_label=str(row["customer_label"]),
            customer_name=str(row["customer_name"]),
            city=str(row["city"]),
            recognition_key=str(row["recognition_key"]),
        )
