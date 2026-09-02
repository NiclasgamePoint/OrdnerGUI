"""Portable catalog construction with no GUI or client dependency."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

from papagui_server.adapters.catalog_extraction import (
    DocumentTextExtractor,
    ExternalCommandRunner,
    ExtractionResourcePolicy,
)
from papagui_server.adapters.catalog_storage import CatalogSqliteWriter
from papagui_server.adapters.catalog_scanner import (
    CatalogResumeMatcher,
    CatalogSourceScanner,
)
from papagui_server.adapters.generations import atomic_json, snapshot_sqlite
from papagui_server.domain.folder_structure import (
    FolderStructureClassifier,
    FolderStructureParser,
)
from papagui_server.domain.models import ServerSettings


CATALOG_SCHEMA_VERSION = "2"
CATALOG_BACKUP_COUNT = 3

__all__ = [
    "DocumentTextExtractor",
    "ExternalCommandRunner",
    "ExtractionResourcePolicy",
    "SqliteCatalogIndexer",
]
class SqliteCatalogIndexer:
    """Build a staged v2 catalog and atomically activate it with three backups."""

    def __init__(self, data_path: Path, extractor: DocumentTextExtractor | None = None) -> None:
        self.data_path = data_path.resolve()
        self.catalog_root = self.data_path / "index" / "catalog"
        self.active_path = self.catalog_root / "active.db"
        self.build_root = self.catalog_root / "builds"
        self.backup_root = self.catalog_root / "backups"
        self.resume_path = self.build_root / "resume.db"
        self.resume_state_path = self.build_root / "resume-state.json"
        self.extractor = extractor or DocumentTextExtractor()
        self._writer = CatalogSqliteWriter()
        self._scanner = CatalogSourceScanner()
        self._resume_matcher = CatalogResumeMatcher()

    def build(
        self,
        source_path: Path,
        *,
        source_id: str,
        full_rebuild: bool,
        settings: ServerSettings,
        cancelled: Callable[[], bool],
        progress: Callable[[int, str], None],
    ) -> Path:
        source_path = source_path.resolve()
        if not source_path.is_dir():
            raise ValueError(f"Die Datenquelle ist nicht erreichbar: {source_path}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", source_id):
            raise ValueError("Die source_id ist ungültig.")
        self.build_root.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        settings_fingerprint = hashlib.sha256(
            json.dumps(settings.to_dict(), sort_keys=True).encode("utf-8")
        ).hexdigest()
        resumable = not full_rebuild and self._resume_matches(
            source_path, source_id, settings_fingerprint
        )
        if full_rebuild or not resumable:
            self.resume_path.unlink(missing_ok=True)
            self.resume_state_path.unlink(missing_ok=True)
            incremental = not full_rebuild and self._is_v2_catalog(self.active_path)
            if incremental:
                snapshot_sqlite(self.active_path, self.resume_path)
        build_path = self.resume_path
        connection = sqlite3.connect(build_path)
        connection.row_factory = sqlite3.Row
        try:
            self._writer.initialize(connection)
            parser = FolderStructureParser(
                FolderStructureClassifier(settings.minimum_customer_year)
            )
            self._writer.synchronize_structure(
                connection,
                source_path,
                source_id=source_id,
                parser=parser,
                excluded=settings.excluded_folder_names,
                cancelled=cancelled,
            )
            connection.execute(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES (?, ?)",
                ("schema_version", CATALOG_SCHEMA_VERSION),
            )
            connection.commit()
            atomic_json(
                self.resume_state_path,
                {
                    "source_id": source_id,
                    "source_path": str(source_path),
                    "settings_fingerprint": settings_fingerprint,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            known = {
                str(row["relative_path"]): (int(row["modified_ns"]), int(row["file_size"]))
                for row in connection.execute(
                    "SELECT relative_path, modified_ns, file_size FROM files WHERE source_id=?",
                    (source_id,),
                )
            }
            seen: set[str] = set()
            processed = 0
            for path in self._files(
                source_path,
                settings.excluded_folder_names,
                newest_years_first=settings.newest_years_first,
            ):
                if cancelled():
                    raise InterruptedError("Indexlauf abgebrochen.")
                relative = path.relative_to(source_path).as_posix()
                seen.add(relative)
                stat = path.stat()
                if known.get(relative) == (stat.st_mtime_ns, stat.st_size):
                    self._writer.update_file_classification(
                        connection, source_id, relative, parser.parse_file(source_id, relative)
                    )
                    processed += 1
                    progress(processed, relative)
                    continue
                content = ""
                extension = path.suffix.casefold().lstrip(".")
                eligible = (
                    settings.content_indexing_enabled
                    and extension in settings.indexed_content_types
                    and stat.st_size <= settings.max_file_size_mb * 1024 * 1024
                )
                if eligible:
                    content = self.extractor.extract(path, settings, cancelled)
                self._writer.upsert_file(
                    connection,
                    source_id=source_id,
                    relative_path=relative,
                    path=path,
                    stat=stat,
                    content=content,
                    eligible=eligible,
                    metadata=parser.parse_file(source_id, relative),
                )
                processed += 1
                progress(processed, relative)
                if processed % 25 == 0:
                    connection.commit()
            removed = set(known) - seen
            for relative in removed:
                uri = _source_uri(source_id, relative)
                connection.execute("DELETE FROM file_content_fts WHERE path=?", (uri,))
                connection.execute(
                    "DELETE FROM files WHERE source_id=? AND relative_path=?",
                    (source_id, relative),
                )
            connection.execute(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES (?, ?)",
                ("schema_version", CATALOG_SCHEMA_VERSION),
            )
            connection.execute(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES (?, ?)",
                ("built_at", datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES (?, ?)",
                ("source_id", source_id),
            )
            connection.commit()
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Der erzeugte Katalog ist beschädigt.")
        except InterruptedError:
            connection.commit()
            connection.close()
            raise
        except Exception:
            connection.rollback()
            connection.close()
            raise
        else:
            connection.close()
        self._activate(build_path)
        self.resume_state_path.unlink(missing_ok=True)
        return self.active_path

    def delete(self) -> None:
        if self.catalog_root.exists():
            shutil.rmtree(self.catalog_root)

    def _activate(self, build_path: Path) -> None:
        backups = [
            self.backup_root / f"catalog.backup.{number}.db"
            for number in range(1, CATALOG_BACKUP_COUNT + 1)
        ]
        previous = self.build_root / ".previous-active.db"
        previous.unlink(missing_ok=True)
        if self.active_path.exists():
            snapshot_sqlite(self.active_path, previous)
        try:
            os.replace(build_path, self.active_path)
        except Exception:
            previous.unlink(missing_ok=True)
            raise
        if previous.exists():
            backups[-1].unlink(missing_ok=True)
            for source, destination in zip(
                reversed(backups[:-1]), reversed(backups[1:])
            ):
                if source.exists():
                    os.replace(source, destination)
            os.replace(previous, backups[0])

    def _resume_matches(
        self, source_path: Path, source_id: str, settings_fingerprint: str
    ) -> bool:
        return self._resume_matcher.matches(
            self.resume_path,
            self.resume_state_path,
            source_path=source_path,
            source_id=source_id,
            settings_fingerprint=settings_fingerprint,
            catalog_is_valid=self._is_v2_catalog,
        )

    def _files(
        self,
        root: Path,
        excluded: frozenset[str],
        *,
        newest_years_first: bool = True,
    ):
        return self._scanner.files(
            root, excluded, newest_years_first=newest_years_first
        )


    @staticmethod
    def _is_v2_catalog(path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            row = connection.execute(
                "SELECT value FROM index_metadata WHERE key='schema_version'"
            ).fetchone()
            columns = {item[1] for item in connection.execute("PRAGMA table_info(files)")}
            return row is not None and str(row[0]) == CATALOG_SCHEMA_VERSION and {
                "source_id", "relative_path"
            }.issubset(columns)
        except sqlite3.Error:
            return False
        finally:
            if "connection" in locals():
                connection.close()


def _source_uri(source_id: str, relative_path: str) -> str:
    return f"source://{source_id}/{relative_path}"
