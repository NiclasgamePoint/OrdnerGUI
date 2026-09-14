"""SQLite catalog schema and portable folder/file writer."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3

from papagui_server.domain.folder_structure import FolderMetadata, FolderStructureParser
from papagui_server.domain.document_extraction import ExtractionResult


class CatalogSqliteWriter:
    def initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                source_id TEXT NOT NULL, relative_path TEXT NOT NULL,
                filename TEXT NOT NULL, file_size INTEGER NOT NULL,
                file_type TEXT NOT NULL, modified_date TEXT NOT NULL,
                modified_ns INTEGER NOT NULL, year TEXT NOT NULL DEFAULT '',
                service_type TEXT NOT NULL DEFAULT '',
                customer_name TEXT NOT NULL DEFAULT '', subfolder TEXT NOT NULL DEFAULT '',
                domain_folder TEXT NOT NULL DEFAULT '', time_bucket TEXT NOT NULL DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '', relative_dir TEXT NOT NULL DEFAULT '',
                folder_id INTEGER, project_root_id INTEGER,
                full_text_indexed INTEGER NOT NULL DEFAULT 0,
                content_eligible INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL DEFAULT '', document_key TEXT UNIQUE NOT NULL,
                UNIQUE(source_id, relative_path)
            );
            CREATE TABLE IF NOT EXISTS index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_roots (
                id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, relative_path TEXT NOT NULL,
                service_type TEXT NOT NULL, year INTEGER NOT NULL,
                customer_label TEXT NOT NULL, customer_name TEXT NOT NULL,
                city TEXT NOT NULL DEFAULT '', recognition_key TEXT NOT NULL,
                UNIQUE(source_id, relative_path)
            );
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, relative_path TEXT NOT NULL,
                name TEXT NOT NULL, parent_id INTEGER, domain_folder TEXT NOT NULL DEFAULT '',
                time_bucket TEXT NOT NULL DEFAULT '', project_name TEXT NOT NULL DEFAULT '',
                project_root_id INTEGER, UNIQUE(source_id, relative_path)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS file_content_fts USING fts5(
                path UNINDEXED, content, tokenize='unicode61 remove_diacritics 2'
            );
            CREATE INDEX IF NOT EXISTS idx_files_source_relative ON files(source_id, relative_path);
            CREATE INDEX IF NOT EXISTS idx_files_filename ON files(filename);
            CREATE INDEX IF NOT EXISTS idx_files_customer ON files(customer_name);
            CREATE INDEX IF NOT EXISTS idx_files_year ON files(year);
            CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
            CREATE INDEX IF NOT EXISTS idx_folders_project ON folders(project_root_id);
            CREATE INDEX IF NOT EXISTS idx_projects_recognition ON project_roots(recognition_key);
            """
        )
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(files)")}
        for name, definition in {
            "extraction_status": "TEXT NOT NULL DEFAULT 'unsupported'",
            "extraction_reason": "TEXT NOT NULL DEFAULT 'not_processed'",
            "extraction_version": "TEXT NOT NULL DEFAULT ''",
            "extraction_policy_version": "TEXT NOT NULL DEFAULT ''",
            "last_scan_run": "TEXT NOT NULL DEFAULT ''",
            "extraction_pages_total": "INTEGER",
            "extraction_pages_processed": "INTEGER NOT NULL DEFAULT 0",
            "extraction_priority": "INTEGER NOT NULL DEFAULT 0",
            "domain_folder": "TEXT NOT NULL DEFAULT ''",
            "time_bucket": "TEXT NOT NULL DEFAULT ''",
            "project_name": "TEXT NOT NULL DEFAULT ''",
            "relative_dir": "TEXT NOT NULL DEFAULT ''",
            "folder_id": "INTEGER",
            "project_root_id": "INTEGER",
        }.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_files_folder_id ON files(folder_id);
            CREATE INDEX IF NOT EXISTS idx_files_project_root_id ON files(project_root_id);
            CREATE INDEX IF NOT EXISTS idx_files_domain ON files(domain_folder);
            CREATE INDEX IF NOT EXISTS idx_files_time_bucket ON files(time_bucket);
            """
        )

        # FTS5's UNINDEXED path column is not a B-tree index. Build this lookup
        # once per staged connection, including catalogs written by older versions.
        connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS content_rowids(path TEXT PRIMARY KEY, fts_rowid INTEGER NOT NULL)"
        )
        connection.execute("DELETE FROM content_rowids")
        connection.execute("INSERT INTO content_rowids SELECT path,rowid FROM file_content_fts")

    def upsert_file(
        self,
        connection: sqlite3.Connection,
        *,
        source_id: str,
        relative_path: str,
        path: Path,
        stat: os.stat_result,
        content: str,
        eligible: bool,
        metadata: FolderMetadata,
        extraction: ExtractionResult | None = None,
    ) -> None:
        uri = f"source://{source_id}/{relative_path}"
        root = metadata.project_root
        folder_id, project_root_id = self.classification_ids(connection, source_id, metadata)
        document_key = hashlib.sha256(f"{source_id}\0{relative_path}".encode()).hexdigest()
        content_hash = (
            extraction.content_hash
            if extraction
            else hashlib.sha256(content.encode()).hexdigest()
            if content
            else ""
        )
        connection.execute(
            """
            INSERT INTO files(
                path, source_id, relative_path, filename, file_size, file_type,
                modified_date, modified_ns, year, service_type, customer_name,
                subfolder, domain_folder, time_bucket, project_name, relative_dir,
                folder_id, project_root_id, full_text_indexed, content_eligible,
                content_hash, document_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, relative_path) DO UPDATE SET
                path=excluded.path, filename=excluded.filename, file_size=excluded.file_size,
                file_type=excluded.file_type, modified_date=excluded.modified_date,
                modified_ns=excluded.modified_ns, year=excluded.year,
                service_type=excluded.service_type, customer_name=excluded.customer_name,
                subfolder=excluded.subfolder, domain_folder=excluded.domain_folder,
                time_bucket=excluded.time_bucket, project_name=excluded.project_name,
                relative_dir=excluded.relative_dir, folder_id=excluded.folder_id,
                project_root_id=excluded.project_root_id,
                full_text_indexed=excluded.full_text_indexed,
                content_eligible=excluded.content_eligible,
                content_hash=excluded.content_hash, document_key=excluded.document_key
            """,
            (
                uri,
                source_id,
                relative_path,
                path.name,
                stat.st_size,
                path.suffix.casefold().lstrip("."),
                datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                stat.st_mtime_ns,
                str(root.year) if root else "",
                root.service_type if root else metadata.domain_folder,
                root.customer_name if root else "",
                Path(relative_path).parts[3] if len(Path(relative_path).parts) > 4 and root else "",
                metadata.domain_folder,
                metadata.time_bucket,
                metadata.project_name,
                metadata.source.relative_path if metadata.source else "",
                folder_id,
                project_root_id,
                int(bool(content)),
                int(eligible),
                content_hash,
                document_key,
            ),
        )
        if extraction is not None:
            connection.execute(
                "UPDATE files SET extraction_status=?, extraction_reason=?, extraction_version=?, "
                "extraction_pages_total=?, extraction_pages_processed=?, extraction_priority=? WHERE path=?",
                (
                    extraction.status,
                    extraction.reason,
                    extraction.artifact_hash or extraction.version_hash,
                    extraction.pages_total,
                    extraction.pages_processed,
                    document_priority(path.name, content),
                    uri,
                ),
            )
        self.remove_content(connection, uri)
        if content:
            cursor = connection.execute(
                "INSERT INTO file_content_fts(path, content) VALUES (?, ?)", (uri, content)
            )
            connection.execute("INSERT INTO content_rowids VALUES (?,?)", (uri, cursor.lastrowid))

    @staticmethod
    def remove_content(connection: sqlite3.Connection, uri: str) -> None:
        connection.execute(
            "DELETE FROM file_content_fts WHERE rowid=(SELECT fts_rowid FROM content_rowids WHERE path=?)",
            (uri,),
        )
        connection.execute("DELETE FROM content_rowids WHERE path=?", (uri,))

    def update_file_classification(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        relative_path: str,
        metadata: FolderMetadata,
    ) -> None:
        folder_id, project_root_id = self.classification_ids(connection, source_id, metadata)
        root = metadata.project_root
        connection.execute(
            """
            UPDATE files SET domain_folder=?, time_bucket=?, project_name=?, relative_dir=?,
                folder_id=?, project_root_id=?, service_type=?, year=?, customer_name=?
            WHERE source_id=? AND relative_path=?
            """,
            (
                metadata.domain_folder,
                metadata.time_bucket,
                metadata.project_name,
                metadata.source.relative_path if metadata.source else "",
                folder_id,
                project_root_id,
                root.service_type if root else metadata.domain_folder,
                str(root.year) if root else "",
                root.customer_name if root else "",
                source_id,
                relative_path,
            ),
        )

    @staticmethod
    def classification_ids(
        connection: sqlite3.Connection, source_id: str, metadata: FolderMetadata
    ) -> tuple[int | None, int | None]:
        folder_id = project_root_id = None
        if metadata.source is not None:
            row = connection.execute(
                "SELECT id FROM folders WHERE source_id=? AND relative_path=?",
                (source_id, metadata.source.relative_path),
            ).fetchone()
            folder_id = int(row[0]) if row else None
        if metadata.project_root is not None:
            row = connection.execute(
                "SELECT id FROM project_roots WHERE source_id=? AND relative_path=?",
                (source_id, metadata.project_root.source.relative_path),
            ).fetchone()
            project_root_id = int(row[0]) if row else None
        return folder_id, project_root_id

    def synchronize_structure(
        self,
        connection: sqlite3.Connection,
        source_path: Path,
        *,
        source_id: str,
        parser: FolderStructureParser,
        excluded: frozenset[str],
        cancelled: Callable[[], bool],
    ) -> None:
        directories: list[str] = []
        for current, children, _files in os.walk(source_path, followlinks=False):
            if cancelled():
                raise InterruptedError("Indexlauf abgebrochen.")
            children[:] = [name for name in sorted(children) if name.casefold() not in excluded]
            relative = Path(current).relative_to(source_path).as_posix()
            if relative != ".":
                directories.append(relative)
        metadata = [parser.parse_directory(source_id, item) for item in directories]
        roots = {
            item.project_root.source.relative_path: item.project_root
            for item in metadata
            if item.project_root is not None
        }
        connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS seen_project_roots(relative_path TEXT PRIMARY KEY)"
        )
        connection.execute("DELETE FROM seen_project_roots")
        for relative, root in sorted(roots.items()):
            connection.execute(
                """
                INSERT INTO project_roots(source_id, relative_path, service_type, year,
                    customer_label, customer_name, city, recognition_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, relative_path) DO UPDATE SET
                    service_type=excluded.service_type, year=excluded.year,
                    customer_label=excluded.customer_label,
                    customer_name=excluded.customer_name, city=excluded.city,
                    recognition_key=excluded.recognition_key
                """,
                (
                    source_id,
                    relative,
                    root.service_type,
                    root.year,
                    root.customer_label,
                    root.customer_name,
                    root.city,
                    root.recognition_key,
                ),
            )
            connection.execute("INSERT OR IGNORE INTO seen_project_roots VALUES (?)", (relative,))
        connection.execute(
            "DELETE FROM project_roots WHERE source_id=? AND relative_path NOT IN "
            "(SELECT relative_path FROM seen_project_roots)",
            (source_id,),
        )
        connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS seen_folders(relative_path TEXT PRIMARY KEY)"
        )
        connection.execute("DELETE FROM seen_folders")
        for item in metadata:
            assert item.source is not None
            parent_id = (
                self._related_id(connection, "folders", source_id, item.parent.relative_path)
                if item.parent
                else None
            )
            project_root_id = (
                self._related_id(
                    connection,
                    "project_roots",
                    source_id,
                    item.project_root.source.relative_path,
                )
                if item.project_root
                else None
            )
            connection.execute(
                """
                INSERT INTO folders(source_id, relative_path, name, parent_id,
                    domain_folder, time_bucket, project_name, project_root_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, relative_path) DO UPDATE SET
                    name=excluded.name, parent_id=excluded.parent_id,
                    domain_folder=excluded.domain_folder, time_bucket=excluded.time_bucket,
                    project_name=excluded.project_name, project_root_id=excluded.project_root_id
                """,
                (
                    source_id,
                    item.source.relative_path,
                    item.name,
                    parent_id,
                    item.domain_folder,
                    item.time_bucket,
                    item.project_name,
                    project_root_id,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO seen_folders VALUES (?)", (item.source.relative_path,)
            )
        connection.execute(
            "DELETE FROM folders WHERE source_id=? AND relative_path NOT IN "
            "(SELECT relative_path FROM seen_folders)",
            (source_id,),
        )

    @staticmethod
    def _related_id(
        connection: sqlite3.Connection, table: str, source_id: str, relative_path: str
    ) -> int | None:
        row = connection.execute(
            f"SELECT id FROM {table} WHERE source_id=? AND relative_path=?",
            (source_id, relative_path),
        ).fetchone()
        return int(row[0]) if row else None


def document_priority(filename: str, content: str) -> int:
    """Cheap triage combines document type, contact context and text quality."""
    name = filename.casefold()
    text = content[:12_000].casefold()
    important = (
        "anschreiben",
        "angebot",
        "auftrag",
        "vertrag",
        "kontakt",
        "adress",
        "bauherr",
        "vollmacht",
    )
    technical = ("berechnung", "leistungsverzeichnis", "statik", "norm", "mengen", "aufmaß")
    score = sum(15 for word in important if word in name)
    score += sum(4 for word in important if word in text)
    score += sum(
        5 for word in ("telefon", "e-mail", "ansprechpartner", "auftraggeber") if word in text
    )
    score -= sum(10 for word in technical if word in name)
    score -= sum(2 for word in technical if word in text)
    letters = 0
    for character in text:
        letters += character.isalpha()
        if letters >= 500:
            break  # Text-quality contribution is capped; remaining letters cannot change it.
    return score + letters // 100
