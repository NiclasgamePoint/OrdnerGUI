import sqlite3
from pathlib import Path
from datetime import datetime
import os
from typing import Callable, List, Dict, Optional
import subprocess
import shutil
import time
import re
import json
import hashlib
import importlib.util
import logging
import warnings
from app import __version__
from app.core.config import IndexOptions, RIPGREP_AVAILABLE
from app.core.folder_structure import FolderStructureClassifier
from app.core.search_models import SearchFilters, SearchPage
from app.services.document_text_indexer import DocumentTextIndexer


logger = logging.getLogger(__name__)


class IndexManager:
    SEARCH_LABEL_SQL = "COALESCE(NULLIF(project_name, ''), NULLIF(customer_name, ''), filename)"
    BINARY_CONTENT_TYPES = {"pdf", "doc", "docx", "xls", "xlsx"}
    TEXT_CONTENT_TYPES = {
        "txt", "csv", "md", "log", "json", "xml", "yaml", "yml", "ini"
    }
    CONTENT_INDEX_TYPES = BINARY_CONTENT_TYPES | TEXT_CONTENT_TYPES
    SCHEMA_VERSION = "4"
    EXTRACTOR_VERSION = "5"
    LEGACY_XLS_TIMEOUT_SECONDS = 15
    LEGACY_DOC_TIMEOUT_SECONDS = 20
    APP_VERSION = __version__
    _VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def __init__(
        self,
        db_path: Path,
        initialize: bool = True,
        options: Optional[IndexOptions] = None,
        content_enabled: bool = True,
    ):
        self.db_path = db_path
        self.conn = None
        self.options = options or IndexOptions()
        self.content_enabled = content_enabled
        self._folder_classifier = FolderStructureClassifier()
        self._content_extractor = (
            DocumentTextIndexer(self.options) if content_enabled else None
        )
        self._ocr_language = None
        if initialize:
            self.init_db()
        else:
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
    
    def init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER,
                file_type TEXT,
                created_date TIMESTAMP,
                modified_date TIMESTAMP,
                year TEXT,
                service_type TEXT,
                customer_name TEXT,
                subfolder TEXT,
                full_text_indexed BOOLEAN DEFAULT 0,
                domain_folder TEXT,
                time_bucket TEXT,
                project_name TEXT,
                relative_dir TEXT,
                index_root TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indexed_roots (
                root_path TEXT PRIMARY KEY,
                last_indexed TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS index_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                path TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                relative_path TEXT,
                parent_path TEXT,
                index_root TEXT NOT NULL,
                project_root_path TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_roots (
                path TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL,
                service_type TEXT NOT NULL,
                year INTEGER NOT NULL,
                customer_label TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                city TEXT NOT NULL DEFAULT '',
                recognition_key TEXT NOT NULL,
                index_root TEXT NOT NULL
            )
        """)

        if self.content_enabled:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS file_content_fts USING fts5(
                    path UNINDEXED,
                    content,
                    tokenize = 'unicode61 remove_diacritics 2'
                )
            """)

        self._ensure_column(cursor, "files", "domain_folder", "TEXT")
        self._ensure_column(cursor, "files", "time_bucket", "TEXT")
        self._ensure_column(cursor, "files", "project_name", "TEXT")
        self._ensure_column(cursor, "files", "relative_dir", "TEXT")
        self._ensure_column(cursor, "files", "index_root", "TEXT")
        self._ensure_column(cursor, "files", "folder_path", "TEXT")
        self._ensure_column(cursor, "files", "modified_ns", "INTEGER")
        self._ensure_column(cursor, "files", "content_hash", "TEXT")
        self._ensure_column(cursor, "files", "content_status", "TEXT")
        self._ensure_column(cursor, "files", "content_error", "TEXT")
        self._ensure_column(cursor, "files", "extractor_version", "TEXT")
        self._ensure_column(cursor, "files", "project_root_path", "TEXT")
        self._ensure_column(cursor, "files", "document_key", "TEXT")
        self._ensure_column(cursor, "files", "source_version", "TEXT")
        self._ensure_column(cursor, "files", "content_eligible", "INTEGER DEFAULT 0")
        self._ensure_column(cursor, "files", "partition_year", "INTEGER")
        self._ensure_column(cursor, "folders", "project_root_path", "TEXT")
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer ON files(customer_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_service_type ON files(service_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_year ON files(year)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_filename ON files(filename)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_domain_folder ON files(domain_folder)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_time_bucket ON files(time_bucket)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_name ON files(project_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_index_root ON files(index_root)
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folder_path ON files(folder_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_project_root ON files(project_root_path)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_document_key ON files(document_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_partition_year ON files(partition_year)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_name ON folders(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folder_project_root ON folders(project_root_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_project_recognition_key ON project_roots(recognition_key)")
        
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _validate_identifier(self, value: str, kind: str):
        if not self._VALID_IDENTIFIER.fullmatch(value):
            raise ValueError(f"Ungültiger {kind}-Name: {value!r}")

    def _ensure_column(self, cursor: sqlite3.Cursor, table: str, column: str, col_type: str):
        self._validate_identifier(table, "Tabellen")
        self._validate_identifier(column, "Spalten")
        cursor.execute(f'PRAGMA table_info("{table}")')
        columns = {row[1] for row in cursor.fetchall()}
        if column not in columns:
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {col_type}')

    def _is_year_bucket(self, value: Optional[str]) -> bool:
        if not value:
            return False
        return len(value) == 4 and value.isdigit()

    def _derive_project_from_filename(self, filename: str) -> str:
        stem = Path(filename).stem.strip()
        separators = [" vom ", " - ", ","]
        for sep in separators:
            if sep in stem:
                left = stem.split(sep, 1)[0].strip()
                if left:
                    return left
        return stem

    def has_index_for_root(self, base_path: Path) -> bool:
        cursor = self.conn.cursor()
        root = str(base_path)
        cursor.execute("SELECT 1 FROM indexed_roots WHERE root_path = ? LIMIT 1", (root,))
        return cursor.fetchone() is not None

    def content_index_needs_rebuild(self, base_path: Path) -> bool:
        if not self.content_enabled:
            return False
        cursor = self.conn.cursor()
        content_types = self.CONTENT_INDEX_TYPES & self.options.indexed_content_types
        if not content_types:
            return False
        placeholders = ", ".join("?" for _ in content_types)
        cursor.execute(
            f"""
            SELECT 1 FROM files
            WHERE index_root = ?
              AND file_type IN ({placeholders})
              AND COALESCE(full_text_indexed, 0) = 0
            LIMIT 1
            """,
            (str(base_path), *sorted(content_types)),
        )
        return cursor.fetchone() is not None

    def get_metadata(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM index_metadata WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row is not None else default

    def set_metadata(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO index_metadata (key, value) VALUES (?, ?)",
            (key, str(value)),
        )

    def index_is_current(self, base_path: Path) -> bool:
        return (
            self.has_index_for_root(base_path)
            and self.get_metadata("schema_version") == self.SCHEMA_VERSION
            and self.get_metadata("extractor_version") == self.EXTRACTOR_VERSION
            and self.get_metadata("options_fingerprint") == self.options.fingerprint()
            and not self.content_index_needs_rebuild(base_path)
        )
    
    def index_directory(
        self,
        base_path: Path,
        replace_existing: bool = False,
        should_cancel: Optional[Callable[[], bool]] = None,
    ):
        warnings.warn(
            "index_directory() ist veraltet. Bitte synchronize_directory() verwenden.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.synchronize_directory(
            base_path,
            full_rebuild=replace_existing,
            should_cancel=should_cancel,
        )

    def synchronize_directory(
        self,
        base_path: Path,
        full_rebuild: bool = False,
        should_cancel: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> int:
        """Incrementally synchronize a source into this (usually staged) index."""
        started_at = time.monotonic()
        base_path = base_path.resolve()
        logger.info("Indexabgleich gestartet: root=%s full_rebuild=%s", base_path, full_rebuild)
        if not base_path.exists() or not base_path.is_dir():
            raise FileNotFoundError(f"Index-Pfad existiert nicht: {base_path}")

        cursor = self.conn.cursor()
        existing_root = self.get_metadata("index_root")
        changed_count = 1 if full_rebuild else 0
        if full_rebuild or (existing_root and existing_root != str(base_path)):
            if self.content_enabled:
                cursor.execute("DELETE FROM file_content_fts")
            cursor.execute("DELETE FROM files")
            cursor.execute("DELETE FROM folders")
            cursor.execute("DELETE FROM project_roots")
            cursor.execute("DELETE FROM indexed_roots")

        cursor.execute("CREATE TEMP TABLE IF NOT EXISTS seen_files (path TEXT PRIMARY KEY)")
        cursor.execute("DELETE FROM seen_files")
        cursor.execute("CREATE TEMP TABLE IF NOT EXISTS seen_folders (path TEXT PRIMARY KEY)")
        cursor.execute("DELETE FROM seen_folders")
        cursor.execute("CREATE TEMP TABLE IF NOT EXISTS seen_project_roots (path TEXT PRIMARY KEY)")
        cursor.execute("DELETE FROM seen_project_roots")

        processed_count = 0
        excluded = self.options.excluded_folder_names
        for current_root, directory_names, file_names in os.walk(base_path):
            if should_cancel is not None and should_cancel():
                raise InterruptedError("Indexierung wurde abgebrochen")

            directory_names[:] = [
                name for name in directory_names if name.casefold() not in excluded
            ]
            # os.walk may return an alias of the requested root (for example
            # /var vs /private/var on macOS or an 8.3 short path on Windows).
            # Keep every persisted and compared path in the same canonical form.
            current_path = Path(current_root).resolve()
            relative = current_path.relative_to(base_path)
            relative_text = "" if relative == Path(".") else str(relative)
            parent_path = str(current_path.parent) if current_path != base_path else ""
            project_root = self._folder_classifier.classify(current_path, base_path)
            project_root_path = project_root.path if project_root is not None else None
            folder_exists = cursor.execute(
                "SELECT 1 FROM folders WHERE path = ?", (str(current_path),)
            ).fetchone()
            cursor.execute(
                """
                INSERT OR REPLACE INTO folders
                (path, name, relative_path, parent_path, index_root, project_root_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(current_path),
                    current_path.name,
                    relative_text,
                    parent_path,
                    str(base_path),
                    project_root_path,
                ),
            )
            cursor.execute("INSERT OR IGNORE INTO seen_folders(path) VALUES (?)", (str(current_path),))
            if project_root is not None and str(current_path) == project_root.path:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO project_roots
                    (path, relative_path, service_type, year, customer_label,
                     customer_name, city, recognition_key, index_root)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_root.path,
                        project_root.relative_path,
                        project_root.service_type,
                        project_root.year,
                        project_root.customer_label,
                        project_root.customer_name,
                        project_root.city,
                        project_root.recognition_key,
                        str(base_path),
                    ),
                )
                cursor.execute(
                    "INSERT OR IGNORE INTO seen_project_roots(path) VALUES (?)",
                    (project_root.path,),
                )
            if folder_exists is None:
                changed_count += 1

            for filename in file_names:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("Indexierung wurde abgebrochen")
                filepath = current_path / filename
                try:
                    stat = filepath.stat()
                except OSError as exc:
                    logger.warning("Datei übersprungen: path=%s error=%s", filepath, exc)
                    continue

                path_text = str(filepath)
                # Report the file before extraction. Some damaged legacy
                # documents can be slow, and the UI must show what is actually
                # being processed rather than the previously completed file.
                if progress_callback is not None:
                    progress_callback(processed_count, path_text)
                cursor.execute("INSERT OR IGNORE INTO seen_files(path) VALUES (?)", (path_text,))
                existing_columns = (
                    "file_size, modified_ns, extractor_version, content_error"
                    if self.content_enabled else "file_size, modified_ns"
                )
                existing = cursor.execute(
                    f"SELECT {existing_columns} FROM files WHERE path = ?",
                    (path_text,),
                ).fetchone()
                content_current = not self.content_enabled or (
                    filepath.suffix.lower().lstrip(".")
                    not in (self.CONTENT_INDEX_TYPES & self.options.indexed_content_types)
                    or existing is not None
                    and existing["extractor_version"] == self.EXTRACTOR_VERSION
                )
                unchanged = (
                    not full_rebuild
                    and existing is not None
                    and existing["file_size"] == stat.st_size
                    and existing["modified_ns"] == stat.st_mtime_ns
                    and content_current
                    and not (self.content_enabled and
                        "PyCryptodome is required" in (existing["content_error"] or "")
                        and importlib.util.find_spec("Crypto") is not None
                    )
                )
                if not unchanged:
                    self._index_file(filepath, base_path, cursor, stat=stat)
                    changed_count += 1

                processed_count += 1
                if progress_callback is not None:
                    progress_callback(processed_count, path_text)
                if processed_count % 250 == 0:
                    self.conn.commit()

        if self.content_enabled:
            cursor.execute(
                "DELETE FROM file_content_fts WHERE path IN "
                "(SELECT path FROM files WHERE path NOT IN (SELECT path FROM seen_files))"
            )
        cursor.execute("DELETE FROM files WHERE path NOT IN (SELECT path FROM seen_files)")
        changed_count += max(cursor.rowcount, 0)
        cursor.execute("DELETE FROM folders WHERE path NOT IN (SELECT path FROM seen_folders)")
        changed_count += max(cursor.rowcount, 0)
        cursor.execute(
            "DELETE FROM project_roots WHERE path NOT IN (SELECT path FROM seen_project_roots)"
        )
        changed_count += max(cursor.rowcount, 0)
        if self.content_enabled:
            changed_count += self._normalize_content_statuses()
        cursor.execute("DELETE FROM indexed_roots")
        cursor.execute(
            "INSERT INTO indexed_roots(root_path, last_indexed) VALUES (?, ?)",
            (str(base_path), datetime.now().isoformat()),
        )
        self.set_metadata("schema_version", self.SCHEMA_VERSION)
        self.set_metadata("extractor_version", self.EXTRACTOR_VERSION)
        self.set_metadata("app_version", self.APP_VERSION)
        self.set_metadata("options_fingerprint", self.options.fingerprint())
        self.set_metadata("index_root", str(base_path))
        self.set_metadata("built_at", datetime.now().isoformat())
        self.set_metadata("build_mode", "full" if full_rebuild else "incremental")
        self.set_metadata("file_count", str(processed_count))
        self.set_metadata("changed_count", str(changed_count))
        self.set_metadata("duration_seconds", f"{time.monotonic() - started_at:.3f}")
        self.conn.commit()
        self.last_change_count = changed_count
        logger.info(
            "Indexabgleich abgeschlossen: files=%s changes=%s duration=%.3fs",
            processed_count,
            changed_count,
            time.monotonic() - started_at,
        )
        return processed_count
    
    def _index_file(
        self,
        filepath: Path,
        base_path: Path,
        cursor: sqlite3.Cursor,
        stat=None,
    ):
        try:
            stat = stat or filepath.stat()
            rel_path = str(filepath.relative_to(base_path))
            parts = rel_path.split(os.sep)
            
            domain_folder = parts[0] if len(parts) > 0 else None
            time_bucket = parts[1] if len(parts) > 1 else None

            project_name = None
            subfolder = None
            if len(parts) >= 4:
                project_name = parts[2]
            elif len(parts) == 3:
                project_name = self._derive_project_from_filename(filepath.name)

            if len(parts) >= 5:
                subfolder = parts[3]

            year = time_bucket if self._is_year_bucket(time_bucket) else None
            service_type = domain_folder
            customer_name = project_name or self._derive_project_from_filename(filepath.name)
            
            file_type = filepath.suffix.lower().lstrip('.')
            relative_dir = str(filepath.parent.relative_to(base_path))
            project_root = self._folder_classifier.classify(filepath.parent, base_path)
            project_root_path = project_root.path if project_root is not None else None
            document_key = hashlib.sha256(
                os.path.normcase(str(filepath.resolve())).encode("utf-8")
            ).hexdigest()
            source_version = hashlib.sha256(
                f"{document_key}:{stat.st_size}:{stat.st_mtime_ns}:"
                f"{self.EXTRACTOR_VERSION}:{self.options.content_fingerprint()}".encode(
                    "utf-8"
                )
            ).hexdigest()
            content_eligible = int(
                file_type in (self.CONTENT_INDEX_TYPES & self.options.indexed_content_types)
            )
            partition_year = int(year) if year else datetime.fromtimestamp(stat.st_mtime).year
            
            cursor.execute("""
                INSERT OR REPLACE INTO files 
                (path, filename, file_size, file_type, created_date, modified_date, 
                 year, service_type, customer_name, subfolder,
                 domain_folder, time_bucket, project_name, relative_dir, index_root,
                 full_text_indexed, folder_path, modified_ns, content_hash,
                 content_status, content_error, extractor_version, project_root_path,
                 document_key, source_version, content_eligible, partition_year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(filepath),
                filepath.name,
                stat.st_size,
                file_type,
                datetime.fromtimestamp(stat.st_ctime).isoformat(),
                datetime.fromtimestamp(stat.st_mtime).isoformat(),
                year,
                service_type,
                customer_name,
                subfolder,
                domain_folder,
                time_bucket,
                project_name,
                relative_dir,
                str(base_path),
                0,
                str(filepath.parent),
                stat.st_mtime_ns,
                "",
                "not_applicable",
                "",
                "",
                project_root_path,
                document_key,
                source_version,
                content_eligible,
                partition_year,
            ))

            if self.content_enabled and content_eligible:
                cursor.execute("DELETE FROM file_content_fts WHERE path = ?", (str(filepath),))
                max_bytes = self.options.max_file_size_mb * 1024 * 1024
                if stat.st_size > max_bytes:
                    content = ""
                    status = "skipped_large"
                    error = f"Datei größer als {self.options.max_file_size_mb} MB"
                else:
                    content, status, error = self._extract_document_with_status(
                        filepath, file_type
                    )
                if content:
                    cursor.execute(
                        "INSERT INTO file_content_fts (path, content) VALUES (?, ?)",
                        (str(filepath), content),
                    )
                cursor.execute(
                    """
                    UPDATE files
                    SET full_text_indexed = 1,
                        content_hash = ?,
                        content_status = ?,
                        content_error = ?,
                        extractor_version = ?
                    WHERE path = ?
                    """,
                    (
                        self._hash_file(filepath) if stat.st_size <= max_bytes else "",
                        status,
                        error,
                        self.EXTRACTOR_VERSION,
                        str(filepath),
                    ),
                )
        except Exception:
            logger.exception("Fehler beim Indexieren: path=%s", filepath)

    def _hash_file(self, filepath: Path) -> str:
        digest = hashlib.sha256()
        with filepath.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _limit_text(self, text: str) -> str:
        return text[:self.options.max_extracted_characters]

    def _extract_document_with_status(
        self, filepath: Path, file_type: str
    ) -> tuple[str, str, str]:
        if self._content_extractor is None:
            return "", "not_applicable", ""
        return self._content_extractor.extract(filepath)

    def _normalize_content_statuses(self) -> int:
        """Migrate older generic errors into actionable diagnostic categories."""
        changed = 0
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE files SET content_status = 'encrypted'
            WHERE content_status = 'error'
              AND (LOWER(content_error) LIKE '%encrypted%'
                   OR LOWER(content_error) LIKE '%password%')
            """
        )
        changed += max(cursor.rowcount, 0)
        cursor.execute(
            """
            UPDATE files SET content_status = 'timeout'
            WHERE content_status = 'error'
              AND (LOWER(content_error) LIKE '%timed out%'
                   OR LOWER(content_error) LIKE '%futures unfinished%'
                   OR LOWER(content_error) LIKE '%ocr-zeitlimit%')
            """
        )
        changed += max(cursor.rowcount, 0)
        return changed

    def search_customers(self, query: str, limit: Optional[int] = None) -> List[Dict]:
        cursor = self.conn.cursor()
        
        q = f"%{query}%"
        sql = f"""
            SELECT
                {self.SEARCH_LABEL_SQL} AS customer_name,
                domain_folder AS service_type,
                time_bucket AS year,
                COUNT(*) AS file_count
            FROM files
            WHERE (
                {self.SEARCH_LABEL_SQL} LIKE ?
                OR relative_dir LIKE ?
                OR domain_folder LIKE ?
            )
            GROUP BY customer_name, service_type, year
            ORDER BY customer_name
        """
        params = [q, q, q]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor.execute(sql, params)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'customer_name': row['customer_name'],
                'service_type': row['service_type'],
                'year': row['year'],
                'file_count': row['file_count']
            })
        
        return results

    def search_folders(self, query: str, limit: Optional[int] = None) -> List[Dict]:
        return self.search_folders_page(query, SearchFilters(), 1, limit or 200).items

    def _metadata_filter_clause(
        self, filters: SearchFilters, alias: str = "files"
    ) -> tuple[str, list[str]]:
        clauses = []
        params = []
        if filters.domain_folder:
            clauses.append(f"{alias}.domain_folder = ? COLLATE NOCASE")
            params.append(filters.domain_folder)
        if filters.year:
            clauses.append(f"{alias}.time_bucket = ? COLLATE NOCASE")
            params.append(filters.year)
        if filters.file_type:
            clauses.append(f"{alias}.file_type = ? COLLATE NOCASE")
            params.append(filters.file_type.lstrip("."))
        return (" AND " + " AND ".join(clauses) if clauses else "", params)

    def get_search_facets(self) -> Dict[str, List[str]]:
        values = {}
        for key, column in (
            ("domains", "domain_folder"),
            ("years", "time_bucket"),
            ("file_types", "file_type"),
        ):
            rows = self.conn.execute(
                f"""
                SELECT DISTINCT {column} FROM files
                WHERE COALESCE({column}, '') <> ''
                ORDER BY {column} COLLATE NOCASE
                """
            )
            values[key] = [str(row[0]) for row in rows]
        values["years"] = sorted(
            values["years"], key=lambda value: (not value.isdigit(), value), reverse=True
        )
        return values

    def search_folders_page(
        self,
        query: str,
        filters: SearchFilters,
        page: int = 1,
        page_size: int = 25,
    ) -> SearchPage:
        from app.core.fuzzy_search import (
            SearchField,
            fuzzy_record_score,
            search_tokens,
        )
        from app.core.search_models import SearchSort

        cursor = self.conn.cursor()
        tokens = search_tokens(query)
        if not tokens:
            return SearchPage([], 0, page, page_size)

        filter_sql, filter_params = self._metadata_filter_clause(filters, "meta")
        eligibility_sql = ""
        eligibility_params: list[str] = []
        if filter_sql:
            eligibility_sql = f"""
                AND EXISTS (
                    SELECT 1 FROM files meta
                    WHERE (
                        meta.folder_path = folder.path
                        OR meta.project_root_path = folder.project_root_path
                    )
                    {filter_sql}
                )
            """
            eligibility_params = [*filter_params]
        searchable = (
            "folder.name",
            "folder.relative_path",
            "folder.path",
            "project.customer_label",
            "project.customer_name",
            "project.city",
            "project.service_type",
            "project.year",
        )
        base_sql = f"""
            SELECT folder.path, folder.name, folder.relative_path,
                   COALESCE(NULLIF(folder.project_root_path, ''), folder.path) AS canonical_path,
                   project.customer_label, project.customer_name, project.city,
                   project.service_type, project.year,
                   (
                       SELECT MAX(candidate_file.modified_date)
                       FROM files candidate_file
                       WHERE candidate_file.project_root_path =
                           COALESCE(NULLIF(folder.project_root_path, ''), folder.path)
                          OR candidate_file.folder_path =
                           COALESCE(NULLIF(folder.project_root_path, ''), folder.path)
                   ) AS last_modified
            FROM folders folder
            JOIN project_roots project ON project.path =
                COALESCE(NULLIF(folder.project_root_path, ''), folder.path)
            WHERE COALESCE(folder.project_root_path, '') <> ''
            AND (
                ? = 1
                OR folder.path = folder.project_root_path
            )
            {eligibility_sql}
        """
        base_params: list = [
            int(filters.include_subfolders),
            *eligibility_params,
        ]

        # Fast path: every query word must occur as an exact substring in at
        # least one indexed name/path field. SQLite reduces the candidate set
        # before Python performs any scoring.
        token_clauses = []
        exact_params: list[str] = []
        for token in tokens:
            token_clauses.append(
                "("
                + " OR ".join(
                    f"COALESCE({field}, '') LIKE ?" for field in searchable
                )
                + ")"
            )
            exact_params.extend([f"%{token}%"] * len(searchable))
        rows = cursor.execute(
            f"{base_sql} AND {' AND '.join(token_clauses)}",
            (*base_params, *exact_params),
        ).fetchall()

        ranked_by_path: dict[str, tuple[float, str, int, str]] = {}
        if rows:
            for row in rows:
                canonical_path = str(row["canonical_path"])
                ranked_by_path[canonical_path] = (
                    1.0,
                    str(row["customer_label"] or row["name"]).casefold(),
                    int(row["year"] or 0),
                    str(row["last_modified"] or ""),
                )
        else:
            # Typo fallback: ask SQLite for at most five plausible prefix
            # matches. Only this bounded set receives the costlier fuzzy score.
            prefix_clauses = []
            fuzzy_params: list[str] = []
            for token in tokens:
                prefix = token[: min(3, len(token))]
                prefix_clauses.append(
                    "("
                    + " OR ".join(
                        f"COALESCE({field}, '') LIKE ?" for field in searchable
                    )
                    + ")"
                )
                fuzzy_params.extend([f"%{prefix}%"] * len(searchable))
            candidates = cursor.execute(
                f"{base_sql} AND ({' OR '.join(prefix_clauses)}) LIMIT 5",
                (*base_params, *fuzzy_params),
            ).fetchall()
            for row in candidates:
                score = fuzzy_record_score(query, [
                    SearchField(row["name"], 1.08),
                    SearchField(row["relative_path"], 0.94),
                    SearchField(row["customer_label"], 1.12),
                    SearchField(row["customer_name"], 1.12),
                    SearchField(row["city"], 1.08),
                    SearchField(row["service_type"], 1.04),
                    SearchField(row["year"], 0.92),
                    SearchField(row["path"], 0.86),
                ])
                if score is not None:
                    canonical_path = str(row["canonical_path"])
                    ranked_by_path[canonical_path] = (
                        score,
                        str(row["customer_label"] or row["name"]).casefold(),
                        int(row["year"] or 0),
                        str(row["last_modified"] or ""),
                    )

        ranked = [
            (score, name, year, modified, path)
            for path, (score, name, year, modified) in ranked_by_path.items()
        ]
        if filters.sort_order == SearchSort.DATE:
            ranked.sort(key=lambda item: (item[1], item[4]))
            ranked.sort(key=lambda item: item[3], reverse=True)
            ranked.sort(key=lambda item: item[2], reverse=True)
        elif filters.sort_order == SearchSort.ALPHABETICAL:
            ranked.sort(key=lambda item: (item[1], item[4]))
        else:
            ranked.sort(key=lambda item: (-item[0], item[1], item[4]))
        total = len(ranked)
        offset = max(0, page - 1) * page_size
        selected_paths = [item[4] for item in ranked[offset:offset + page_size]]
        items = []
        for path in selected_paths:
            row = cursor.execute(
                """
                SELECT folders.path, folders.name, folders.relative_path,
                    (SELECT COUNT(*) FROM files
                     WHERE files.folder_path = folders.path
                        OR files.path LIKE folders.path || ? || '%') AS file_count
                FROM folders WHERE folders.path = ?
                """,
                (os.sep, path),
            ).fetchone()
            if row is not None:
                items.append({
                    "folder_path": row["path"],
                    "folder_name": row["name"],
                    "relative_path": row["relative_path"],
                    "file_count": row["file_count"],
                })
        return SearchPage(items, total, page, page_size)

    def get_folder_search_entry(
        self, folder_path: str, filters: SearchFilters
    ) -> Optional[Dict]:
        canonical_path = self._canonical_folder_path(folder_path)
        filter_sql, filter_params = self._metadata_filter_clause(filters, "f")
        row = self.conn.execute(
            f"""
            SELECT folders.path, folders.name, folders.relative_path,
                (SELECT COUNT(*) FROM files
                 WHERE files.folder_path = folders.path
                    OR files.path LIKE folders.path || ? || '%') AS file_count
            FROM folders
            WHERE folders.path = ?
              AND EXISTS (
                SELECT 1 FROM files f
                WHERE (f.folder_path = folders.path OR f.path LIKE folders.path || ? || '%')
                {filter_sql}
              )
            """,
            (os.sep, canonical_path, os.sep, *filter_params),
        ).fetchone()
        if row is None:
            return None
        return {
            "folder_path": row["path"],
            "folder_name": row["name"],
            "relative_path": row["relative_path"],
            "file_count": row["file_count"],
        }

    def _canonical_folder_path(self, folder_path: str) -> str:
        path = str(Path(folder_path).resolve())
        row = self.conn.execute(
            "SELECT project_root_path FROM folders WHERE path = ?",
            (path,),
        ).fetchone()
        if row is not None and row[0]:
            return str(row[0])
        return path

    def get_folder_details(self, folder_path: str) -> Dict:
        cursor = self.conn.cursor()
        path = self._canonical_folder_path(folder_path)
        pattern = f"{path}{os.sep}%"
        cursor.execute(
            """
            SELECT COUNT(*) AS file_count, MAX(modified_date) AS last_modified,
                   COALESCE(SUM(file_size), 0) AS total_size
            FROM files WHERE folder_path = ? OR path LIKE ?
            """,
            (path, pattern),
        )
        info = dict(cursor.fetchone())
        cursor.execute(
            """
            SELECT path, filename, file_type, file_size, modified_date,
                   domain_folder, time_bucket, relative_dir
            FROM files WHERE folder_path = ? OR path LIKE ?
            ORDER BY relative_dir, filename COLLATE NOCASE
            """,
            (path, pattern),
        )
        info["files"] = [dict(row) for row in cursor.fetchall()]
        info["folder_name"] = Path(path).name
        info["folder_path"] = path
        info["service_types"] = sorted({
            row["domain_folder"] for row in info["files"] if row["domain_folder"]
        })
        cursor.execute(
            """
            SELECT path, name, parent_path, relative_path
            FROM folders
            WHERE path LIKE ?
            ORDER BY relative_path COLLATE NOCASE
            """,
            (pattern,),
        )
        nodes: dict[str, dict] = {}
        roots: list[dict] = []
        for row in cursor.fetchall():
            node = {
                "path": str(row["path"]),
                "name": str(row["name"]),
                "relative_path": str(row["relative_path"] or ""),
                "children": [],
            }
            nodes[node["path"]] = node
            parent_path = str(row["parent_path"] or "")
            if parent_path == path:
                roots.append(node)
            elif parent_path in nodes:
                nodes[parent_path]["children"].append(node)
            else:
                roots.append(node)
        info["subfolders"] = roots
        return info

    def get_folder_summary(self, folder_path: str) -> Dict:
        """Return lightweight folder metadata without loading every file row."""
        path = self._canonical_folder_path(folder_path)
        pattern = f"{path}{os.sep}%"
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS file_count,
                MAX(modified_date) AS last_modified,
                COALESCE(SUM(file_size), 0) AS total_size,
                GROUP_CONCAT(DISTINCT domain_folder) AS service_types,
                GROUP_CONCAT(DISTINCT time_bucket) AS time_buckets
            FROM files
            WHERE folder_path = ? OR path LIKE ?
            """,
            (path, pattern),
        ).fetchone()
        return {
            "folder_path": path,
            "folder_name": Path(path).name,
            "file_count": int(row["file_count"] or 0),
            "last_modified": row["last_modified"],
            "total_size": int(row["total_size"] or 0),
            "service_types": sorted(
                value for value in (row["service_types"] or "").split(",") if value
            ),
            "time_buckets": sorted(
                value for value in (row["time_buckets"] or "").split(",") if value
            ),
        }

    def list_project_roots(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT path, relative_path, service_type, year, customer_label,
                   customer_name, city, recognition_key
            FROM project_roots
            ORDER BY recognition_key, path COLLATE NOCASE
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def indexed_text_for_folder(
        self,
        folder_path: str,
        max_characters: int = 250_000,
    ) -> str:
        path = self._canonical_folder_path(folder_path)
        rows = self.conn.execute(
            """
            SELECT file_content_fts.content
            FROM file_content_fts
            WHERE path LIKE ?
            ORDER BY path COLLATE NOCASE
            """,
            (f"{path}{os.sep}%",),
        )
        chunks: list[str] = []
        size = 0
        for row in rows:
            content = str(row[0] or "")
            if not content:
                continue
            remaining = max_characters - size
            if remaining <= 0:
                break
            chunks.append(content[:remaining])
            size += min(len(content), remaining)
        return "\n".join(chunks)

    def indexed_documents_for_folder(
        self,
        folder_path: str,
        max_documents: int = 24,
        max_characters_per_document: int = 80_000,
    ) -> list[dict]:
        """Return extracted text with its source boundary intact."""
        path = self._canonical_folder_path(folder_path)
        rows = self.conn.execute(
            """
            SELECT files.path, files.filename, files.file_type,
                   file_content_fts.content
            FROM file_content_fts
            JOIN files ON files.path = file_content_fts.path
            WHERE files.project_root_path = ?
               OR files.folder_path = ?
               OR files.path LIKE ?
            ORDER BY files.modified_date DESC, files.path COLLATE NOCASE
            LIMIT ?
            """,
            (path, path, f"{path}{os.sep}%", max(1, int(max_documents))),
        ).fetchall()
        return [
            {
                "path": str(row["path"]),
                "filename": str(row["filename"]),
                "file_type": str(row["file_type"] or "").casefold(),
                "content": str(row["content"] or "")[:max_characters_per_document],
            }
            for row in rows
            if str(row["content"] or "").strip()
        ]
    
    def get_customer_details(self, customer_name: str) -> Dict:
        cursor = self.conn.cursor()

        cursor.execute(
            f"""
            SELECT 
                COUNT(*) as file_count,
                MAX(modified_date) as last_modified,
                SUM(file_size) as total_size
            FROM files
            WHERE {self.SEARCH_LABEL_SQL} = ?
            """,
            (customer_name,)
        )
        
        info = dict(cursor.fetchone())
        
        cursor.execute(
            f"""
            SELECT DISTINCT domain_folder FROM files
            WHERE {self.SEARCH_LABEL_SQL} = ?
            ORDER BY domain_folder
            """,
            (customer_name,)
        )
        
        info['service_types'] = [row['domain_folder'] for row in cursor.fetchall() if row['domain_folder']]
        
        cursor.execute(
            f"""
            SELECT 
                path, filename, file_type, file_size, modified_date,
                domain_folder, time_bucket, project_name, subfolder, relative_dir,
                service_type, year
            FROM files
            WHERE {self.SEARCH_LABEL_SQL} = ?
            ORDER BY time_bucket DESC, domain_folder, subfolder, filename
            """,
            (customer_name,)
        )
        
        info['files'] = [dict(row) for row in cursor.fetchall()]
        info['customer_name'] = customer_name
        
        return info
    
    def search_files(
        self,
        query: str,
        customer_name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        return self.search_files_page(
            query, SearchFilters(), 1, limit or 200, customer_name=customer_name
        ).items

    def search_files_page(
        self,
        query: str,
        filters: SearchFilters,
        page: int = 1,
        page_size: int = 25,
        customer_name: Optional[str] = None,
    ) -> SearchPage:
        cursor = self.conn.cursor()
        filter_sql, filter_params = self._metadata_filter_clause(filters)
        where = "(filename LIKE ? OR relative_dir LIKE ?)" + filter_sql
        params: list = [f"%{query}%", f"%{query}%", *filter_params]
        if customer_name:
            where += f" AND {self.SEARCH_LABEL_SQL} = ?"
            params.append(customer_name)
        total = int(cursor.execute(f"SELECT COUNT(*) FROM files WHERE {where}", params).fetchone()[0])
        offset = max(0, page - 1) * page_size
        sql = f"""
            SELECT * FROM files WHERE {where}
            ORDER BY
                CASE
                    WHEN filename = ? COLLATE NOCASE THEN 0
                    WHEN filename LIKE ? THEN 1
                    WHEN filename LIKE ? THEN 2
                    ELSE 3
                END,
                modified_date DESC, filename COLLATE NOCASE
            LIMIT ? OFFSET ?
        """
        cursor.execute(
            sql,
            [*params, query, f"{query}%", f"%{query}%", page_size, offset],
        )
        return SearchPage([dict(row) for row in cursor.fetchall()], total, page, page_size)

    def _iter_text_search_targets(self, customer_name: Optional[str]) -> List[List[str]]:
        cursor = self.conn.cursor()

        if customer_name:
            cursor.execute(
                f"SELECT DISTINCT path FROM files WHERE {self.SEARCH_LABEL_SQL} = ?",
                (customer_name,)
            )
            paths = [row[0] for row in cursor.fetchall()]
            chunk_size = 200
            return [paths[i:i + chunk_size] for i in range(0, len(paths), chunk_size)]

        cursor.execute("SELECT DISTINCT root_path FROM indexed_roots")
        roots = [row[0] for row in cursor.fetchall()]
        return [[root] for root in roots]

    def _parse_rg_output(self, stdout: str) -> List[Dict]:
        parsed = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data", {})
            path_data = data.get("path", {})
            lines_data = data.get("lines", {})
            file_path = path_data.get("text")
            if not file_path:
                continue
            parsed.append({
                "path": file_path,
                "filename": Path(file_path).name,
                "folder_path": str(Path(file_path).parent),
                "modified_date": "",
                "line": data.get("line_number"),
                "excerpt": (lines_data.get("text") or "").strip(),
                "source": "text",
            })
        return parsed

    def _build_fts_query(self, query: str) -> str:
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)

    def _search_extracted_content(
        self,
        query: str,
        customer_name: Optional[str],
        limit: int = 200,
        filters: Optional[SearchFilters] = None,
    ) -> List[Dict]:
        from app.core.search_models import SearchSort

        filters = filters or SearchFilters()
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        sql = """
            SELECT
                file_content_fts.path AS path,
                files.filename AS filename,
                files.folder_path AS folder_path,
                files.project_root_path AS project_root_path,
                files.modified_date AS modified_date,
                snippet(file_content_fts, 1, '', '', ' … ', 18) AS excerpt
            FROM file_content_fts
            JOIN files ON files.path = file_content_fts.path
            WHERE file_content_fts MATCH ?
        """
        params = [fts_query]
        filter_sql, filter_params = self._metadata_filter_clause(filters)
        sql += filter_sql
        params.extend(filter_params)
        if customer_name:
            sql += f" AND {self.SEARCH_LABEL_SQL} = ?"
            params.append(customer_name)
        if filters.sort_order == SearchSort.DATE:
            sql += " ORDER BY files.modified_date DESC, files.filename COLLATE NOCASE"
        elif filters.sort_order == SearchSort.ALPHABETICAL:
            sql += " ORDER BY files.filename COLLATE NOCASE, files.path COLLATE NOCASE"
        else:
            sql += " ORDER BY rank, files.filename COLLATE NOCASE"
        sql += " LIMIT ?"
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return [
            {
                "path": row["path"],
                "filename": str(row["filename"] or Path(str(row["path"])).name),
                "folder_path": str(
                    row["project_root_path"] or row["folder_path"] or ""
                ),
                "modified_date": str(row["modified_date"] or ""),
                "line": None,
                "excerpt": (row["excerpt"] or "").replace("\n", " ").strip(),
                "source": "document",
            }
            for row in cursor.fetchall()
        ]
    
    def _run_ripgrep(
        self,
        command: List[str],
        should_cancel: Optional[Callable[[], bool]],
    ) -> str:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        deadline = time.monotonic() + 25
        while True:
            try:
                stdout, _ = process.communicate(timeout=0.1)
                return stdout
            except subprocess.TimeoutExpired:
                if should_cancel is not None and should_cancel():
                    process.terminate()
                    try:
                        process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    return ""
                if time.monotonic() >= deadline:
                    process.kill()
                    process.communicate()
                    return ""

    def search_in_text(
        self,
        query: str,
        customer_name: Optional[str] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        limit: int = 200,
        filters: Optional[SearchFilters] = None,
    ) -> List[Dict]:
        try:
            results = self._search_extracted_content(
                query, customer_name, limit=limit, filters=filters
            )
            indexed_result_paths = {result["path"] for result in results}
            if should_cancel is not None and should_cancel():
                return results
            if filters is not None and filters.active:
                return results

            if not RIPGREP_AVAILABLE:
                return results
            rg_path = shutil.which("rg")
            if rg_path is None:
                return results

            target_groups = self._iter_text_search_targets(customer_name)
            if not target_groups:
                return results

            for targets in target_groups:
                if should_cancel is not None and should_cancel():
                    break
                command = [
                    rg_path,
                    '--json',
                    '--line-number',
                    '--no-messages',
                    '--smart-case',
                    '--fixed-strings',
                    '--glob', '*.txt',
                    '--glob', '*.csv',
                    '--glob', '*.md',
                    '--glob', '*.log',
                    '--glob', '*.json',
                    '--glob', '*.xml',
                    '--glob', '*.yaml',
                    '--glob', '*.yml',
                    '--glob', '*.ini',
                    query,
                    *targets,
                ]
                stdout = self._run_ripgrep(command, should_cancel)
                if stdout:
                    results.extend(
                        result
                        for result in self._parse_rg_output(stdout)
                        if result["path"] not in indexed_result_paths
                    )

            unique_results = []
            seen = set()
            for result in results:
                key = (result["path"], result.get("line"))
                if key in seen:
                    continue
                seen.add(key)
                unique_results.append(result)
            from app.core.search_models import SearchSort

            sort_order = (filters or SearchFilters()).sort_order
            if sort_order == SearchSort.DATE:
                unique_results.sort(
                    key=lambda item: (
                        str(item.get("modified_date") or ""),
                        str(item.get("filename") or "").casefold(),
                    ),
                    reverse=True,
                )
            elif sort_order == SearchSort.ALPHABETICAL:
                unique_results.sort(
                    key=lambda item: (
                        str(item.get("filename") or Path(item["path"]).name).casefold(),
                        str(item["path"]).casefold(),
                    )
                )
            return unique_results[:limit]
        except Exception:
            logger.exception("Fehler bei Volltextsuche: query=%r", query)
            return []

    def search_text_page(
        self,
        query: str,
        filters: SearchFilters,
        page: int = 1,
        page_size: int = 25,
        maximum: int = 200,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> SearchPage:
        all_results = self.search_in_text(
            query,
            should_cancel=should_cancel,
            limit=maximum,
            filters=filters,
        )
        offset = max(0, page - 1) * page_size
        return SearchPage(
            all_results[offset:offset + page_size],
            len(all_results),
            page,
            page_size,
        )
    
    def close(self):
        """Schließt die Datenbankverbindung"""
        if self.conn:
            self.conn.close()


if __name__ == "__main__":
    from app.core.config import DB_FILE, get_default_index_source

    index_root = get_default_index_source()
    with IndexManager(DB_FILE) as manager:
        manager.index_directory(index_root)
        results = manager.search_customers("Müller")
        logger.info("Gefundene Kunden: %s", results)
