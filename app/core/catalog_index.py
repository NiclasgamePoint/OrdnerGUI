from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib
import os
import shutil
import sqlite3
import uuid

from app.core.config import IndexOptions
from app.core.content_index import ContentStateRepository, ShardRepository
from app.core.index_layout import IndexLayout
from app.core.index_manager import IndexManager


CATALOG_SCHEMA_VERSION = "5"
CATALOG_BACKUP_COUNT = 3


@dataclass(frozen=True)
class CatalogValidation:
    file_count: int
    folder_count: int
    project_count: int
    generation: str


class CatalogIndexManager(IndexManager):
    """Metadata-only index; document bytes are never opened during its scan."""

    SCHEMA_VERSION = CATALOG_SCHEMA_VERSION

    def __init__(
        self,
        db_path: Path,
        initialize: bool = True,
        options: IndexOptions | None = None,
    ):
        super().__init__(
            db_path,
            initialize=initialize,
            options=options,
            content_enabled=False,
        )

    def init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER,
                file_type TEXT,
                created_date TIMESTAMP,
                modified_date TIMESTAMP,
                modified_ns INTEGER,
                year TEXT,
                service_type TEXT,
                customer_name TEXT,
                subfolder TEXT,
                domain_folder TEXT,
                time_bucket TEXT,
                project_name TEXT,
                relative_dir TEXT,
                index_root TEXT,
                folder_path TEXT,
                project_root_path TEXT,
                document_key TEXT UNIQUE NOT NULL,
                source_version TEXT NOT NULL,
                content_eligible INTEGER NOT NULL DEFAULT 0,
                partition_year INTEGER
            );
            CREATE TABLE IF NOT EXISTS indexed_roots (
                root_path TEXT PRIMARY KEY,
                last_indexed TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS index_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS folders (
                path TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                relative_path TEXT,
                parent_path TEXT,
                index_root TEXT NOT NULL,
                project_root_path TEXT
            );
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
            );
            CREATE INDEX IF NOT EXISTS idx_customer ON files(customer_name);
            CREATE INDEX IF NOT EXISTS idx_service_type ON files(service_type);
            CREATE INDEX IF NOT EXISTS idx_year ON files(year);
            CREATE INDEX IF NOT EXISTS idx_filename ON files(filename);
            CREATE INDEX IF NOT EXISTS idx_domain_folder ON files(domain_folder);
            CREATE INDEX IF NOT EXISTS idx_time_bucket ON files(time_bucket);
            CREATE INDEX IF NOT EXISTS idx_project_name ON files(project_name);
            CREATE INDEX IF NOT EXISTS idx_index_root ON files(index_root);
            CREATE INDEX IF NOT EXISTS idx_folder_path ON files(folder_path);
            CREATE INDEX IF NOT EXISTS idx_file_project_root ON files(project_root_path);
            CREATE INDEX IF NOT EXISTS idx_partition_year ON files(partition_year);
            CREATE INDEX IF NOT EXISTS idx_folders_name ON folders(name);
            CREATE INDEX IF NOT EXISTS idx_folder_project_root
                ON folders(project_root_path);
            CREATE INDEX IF NOT EXISTS idx_project_recognition_key
                ON project_roots(recognition_key);
            """
        )
        self.conn.commit()

    def _index_file(self, filepath: Path, base_path: Path, cursor, stat=None):
        stat = stat or filepath.stat()
        relative = filepath.relative_to(base_path)
        parts = relative.parts
        domain_folder = parts[0] if parts else None
        time_bucket = parts[1] if len(parts) > 1 else None
        project_name = (
            parts[2] if len(parts) >= 4
            else (self._derive_project_from_filename(filepath.name) if len(parts) == 3 else None)
        )
        subfolder = parts[3] if len(parts) >= 5 else None
        year = time_bucket if self._is_year_bucket(time_bucket) else None
        file_type = filepath.suffix.lower().lstrip(".")
        project_root = self._folder_classifier.classify(filepath.parent, base_path)
        document_key = hashlib.sha256(
            os.path.normcase(str(filepath.resolve())).encode("utf-8")
        ).hexdigest()
        source_version = self._source_version(document_key, stat.st_size, stat.st_mtime_ns)
        eligible = int(
            file_type in (self.CONTENT_INDEX_TYPES & self.options.indexed_content_types)
        )
        partition_year = int(year) if year else datetime.fromtimestamp(stat.st_mtime).year
        cursor.execute(
            """
            INSERT OR REPLACE INTO files(
                path,filename,file_size,file_type,created_date,modified_date,modified_ns,
                year,service_type,customer_name,subfolder,domain_folder,time_bucket,
                project_name,relative_dir,index_root,folder_path,project_root_path,
                document_key,source_version,content_eligible,partition_year
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(filepath), filepath.name, stat.st_size, file_type,
                datetime.fromtimestamp(stat.st_ctime).isoformat(),
                datetime.fromtimestamp(stat.st_mtime).isoformat(), stat.st_mtime_ns,
                year, domain_folder,
                project_name or self._derive_project_from_filename(filepath.name),
                subfolder, domain_folder, time_bucket, project_name,
                str(filepath.parent.relative_to(base_path)), str(base_path),
                str(filepath.parent), project_root.path if project_root else None,
                document_key, source_version, eligible, partition_year,
            ),
        )

    def _source_version(self, document_key: str, size: int, modified_ns: int) -> str:
        return hashlib.sha256(
            f"{document_key}:{size}:{modified_ns}:{self.EXTRACTOR_VERSION}:"
            f"{self.options.content_fingerprint()}".encode("utf-8")
        ).hexdigest()

    def synchronize_directory(self, *args, **kwargs) -> int:
        count = super().synchronize_directory(*args, **kwargs)
        contract_changes = self._refresh_content_contract()
        self.last_change_count = getattr(self, "last_change_count", 0) + contract_changes
        self.set_metadata("changed_count", str(self.last_change_count))
        generation = uuid.uuid4().hex
        self.set_metadata("catalog_generation", generation)
        self.set_metadata("options_fingerprint", self.options.catalog_fingerprint())
        self.conn.commit()
        return count

    def index_is_current(self, base_path: Path) -> bool:
        return (
            self.has_index_for_root(base_path)
            and self.get_metadata("schema_version") == self.SCHEMA_VERSION
            and self.get_metadata("options_fingerprint")
            == self.options.catalog_fingerprint()
        )

    def _refresh_content_contract(self):
        fingerprint = self.options.content_fingerprint()
        if self.get_metadata("content_options_fingerprint") == fingerprint:
            return 0
        rows = self.conn.execute(
            "SELECT id,document_key,file_size,modified_ns,file_type FROM files"
        ).fetchall()
        eligible_types = self.CONTENT_INDEX_TYPES & self.options.indexed_content_types
        updates = []
        for row in rows:
            eligible = int(str(row["file_type"] or "") in eligible_types)
            source_version = self._source_version(
                str(row["document_key"]),
                int(row["file_size"] or 0),
                int(row["modified_ns"] or 0),
            )
            updates.append((eligible, source_version, int(row["id"])))
        self.conn.executemany(
            "UPDATE files SET content_eligible=?,source_version=? WHERE id=?",
            updates,
        )
        self.set_metadata("content_options_fingerprint", fingerprint)
        return len(updates)

    def content_candidates(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT document_key, path, source_version, partition_year, file_size,
                   filename, project_root_path
            FROM files
            WHERE content_eligible=1
            ORDER BY partition_year DESC, modified_date DESC, path COLLATE NOCASE
            """
        ).fetchall()

    def reconcile_content_state(
        self,
        state: ContentStateRepository,
        shards: ShardRepository,
        preferred_patterns: list[str],
        priority_documents_per_project: int = 24,
        newest_years_first: bool = True,
    ):
        generation = self.get_metadata("catalog_generation")
        project_document_counts: dict[str, int] = {}
        candidates = self.content_candidates()
        if not newest_years_first:
            candidates = sorted(
                candidates,
                key=lambda row: (int(row["partition_year"] or 0), str(row["path"]).casefold()),
            )
        for row in candidates:
            filename = str(row["filename"] or "").casefold()
            project_document = bool(row["project_root_path"])
            preferred = project_document and any(
                pattern in filename for pattern in preferred_patterns
            )
            project_path = str(row["project_root_path"] or "")
            selected_for_enrichment = (
                project_document
                and project_document_counts.get(project_path, 0)
                < max(1, int(priority_documents_per_project))
            )
            if selected_for_enrichment:
                project_document_counts[project_path] = (
                    project_document_counts.get(project_path, 0) + 1
                )
            priority = 0 if preferred else (10 if selected_for_enrichment else 100)
            moved_from, _changed = state.reconcile_document(
                document_key=str(row["document_key"]),
                path=str(row["path"]),
                source_version=str(row["source_version"]),
                partition_year=(
                    int(row["partition_year"])
                    if row["partition_year"] is not None else None
                ),
                source_size=int(row["file_size"] or 0),
                priority=priority,
                catalog_generation=generation,
            )
            if moved_from:
                shards.delete(str(row["document_key"]), moved_from)
        stale = state.finalize_generation(generation)
        for row in stale:
            shards.delete(str(row["document_key"]), str(row["shard_name"] or ""))


class CatalogStore:
    """Build, validate, activate and rotate only the small catalog database."""

    def __init__(self, layout: IndexLayout):
        self.layout = layout
        self.layout.ensure_directories()

    def create_build_path(self) -> Path:
        return self.layout.catalog_build_dir / f"catalog-{uuid.uuid4().hex}.db"

    def seed_build(self, build_path: Path, incremental: bool):
        if build_path.exists():
            build_path.unlink()
        if not incremental or not self.layout.catalog_path.exists():
            return
        source = sqlite3.connect(self.layout.catalog_path)
        destination = sqlite3.connect(build_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    def validate(self, path: Path) -> CatalogValidation:
        if not path.exists() or path.stat().st_size == 0:
            raise ValueError("Der erzeugte Katalogindex ist leer")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"SQLite-Integritätsfehler: {integrity}")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "file_content_fts" in tables:
                raise ValueError("Der Katalog enthält unerwartet Dokumentinhalte")
            metadata = dict(connection.execute("SELECT key,value FROM index_metadata"))
            if metadata.get("schema_version") != CATALOG_SCHEMA_VERSION:
                raise ValueError("Unbekannte Katalogschema-Version")
            return CatalogValidation(
                file_count=int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
                folder_count=int(connection.execute("SELECT COUNT(*) FROM folders").fetchone()[0]),
                project_count=int(
                    connection.execute("SELECT COUNT(*) FROM project_roots").fetchone()[0]
                ),
                generation=str(metadata.get("catalog_generation") or ""),
            )
        finally:
            connection.close()

    def backup_paths(self) -> list[Path]:
        return [
            self.layout.catalog_backup_dir / f"catalog-{number}.db"
            for number in range(1, CATALOG_BACKUP_COUNT + 1)
        ]

    def activate(self, build_path: Path):
        self.validate(build_path)
        backups = self.backup_paths()
        if backups[-1].exists():
            backups[-1].unlink()
        for source, destination in zip(
            reversed(backups[:-1]), reversed(backups[1:])
        ):
            if source.exists():
                os.replace(source, destination)
        old_moved = False
        try:
            if self.layout.catalog_path.exists():
                os.replace(self.layout.catalog_path, backups[0])
                old_moved = True
            os.replace(build_path, self.layout.catalog_path)
        except Exception:
            if old_moved and backups[0].exists() and not self.layout.catalog_path.exists():
                os.replace(backups[0], self.layout.catalog_path)
            raise

    def available_backups(self) -> list[dict[str, str]]:
        result = []
        for path in self.backup_paths():
            if not path.exists():
                continue
            validation = self.validate(path)
            modified = datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%d.%m.%Y %H:%M"
            )
            result.append({
                "path": str(path),
                "label": f"{modified} · {validation.file_count} Dateien",
            })
        return result

    def create_restore_build(self, backup_path: Path) -> Path:
        resolved = backup_path.resolve()
        if resolved not in {path.resolve() for path in self.backup_paths()}:
            raise ValueError("Ungültige Katalogsicherung")
        if not resolved.exists():
            raise ValueError("Die Katalogsicherung existiert nicht mehr")
        build = self.create_build_path()
        shutil.copy2(resolved, build)
        self.validate(build)
        return build
