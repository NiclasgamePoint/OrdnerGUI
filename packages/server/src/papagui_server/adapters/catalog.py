"""Portable catalog construction with no GUI or client dependency."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import re
import shutil
import sqlite3
import zipfile

from papagui_server.adapters.catalog_extraction import (
    DocumentTextExtractor,
    ExternalCommandRunner,
    ExtractionResourcePolicy,
    SUPPORTED_EXTENSIONS,
    IMAGE_EXTENSIONS,
    extraction_fingerprint,
)
from papagui_server.adapters.catalog_storage import CatalogSqliteWriter
from papagui_server.adapters.extraction_store import ExtractionStore, artifact_identity
from papagui_server.domain.document_extraction import ExtractionBlock, ExtractionResult
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
        self.extraction_store = ExtractionStore(self.data_path / "extraction" / "artifacts.db")
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
        force_extraction: bool = False,
        project_root_ids: list[int] | tuple[int, ...] | None = None,
    ) -> Path:
        source_path = source_path.resolve()
        if not source_path.is_dir():
            raise ValueError("Die Datenquelle ist nicht erreichbar.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", source_id):
            raise ValueError("Die source_id ist ungültig.")
        self.build_root.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        parser_fingerprint = (
            self.extractor.fingerprint(settings)
            if hasattr(self.extractor, "fingerprint")
            else extraction_fingerprint(settings)
        )
        format_fingerprints: dict[str, str] = {}
        selected_projects = set(project_root_ids) if project_root_ids is not None else None
        settings_fingerprint = hashlib.sha256(
            json.dumps([settings.to_dict(), parser_fingerprint], sort_keys=True).encode("utf-8")
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
            root_ids = {
                str(row["relative_path"]): int(row["id"])
                for row in connection.execute(
                    "SELECT id, relative_path FROM project_roots WHERE source_id=?", (source_id,)
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
                metadata = parser.parse_file(source_id, relative)
                extension = path.suffix.casefold().lstrip(".")
                eligible = (
                    settings.content_indexing_enabled
                    and extension in settings.indexed_content_types
                    and extension in SUPPORTED_EXTENSIONS
                    and stat.st_size <= settings.max_file_size_mb * 1024 * 1024
                )
                content_hash = ""
                policy = ExtractionResourcePolicy.for_document(path, settings)
                ocr_budget = (
                    policy.ocr_pages if extension == "pdf" or extension in IMAGE_EXTENSIONS else 0
                )
                image_preferred = extension in IMAGE_EXTENSIONS and any(
                    word in path.stem.casefold()
                    for word in (
                        "scan",
                        "kontakt",
                        "anschreiben",
                        "auftrag",
                        "angebot",
                        "vertrag",
                        "dokument",
                    )
                )
                if extension not in format_fingerprints:
                    format_fingerprints[extension] = (
                        self.extractor.fingerprint(settings, extension)
                        if isinstance(self.extractor, DocumentTextExtractor)
                        else parser_fingerprint
                    )
                version_hash = hashlib.sha256(
                    f"{format_fingerprints[extension]}\0{extension}\0{ocr_budget}\0{image_preferred}".encode()
                ).hexdigest()
                if not settings.content_indexing_enabled:
                    extraction = ExtractionResult(
                        status="unsupported", reason="content_indexing_disabled"
                    )
                elif stat.st_size > settings.max_file_size_mb * 1024 * 1024:
                    extraction = ExtractionResult(status="too_large", reason="file_size_budget")
                elif not eligible:
                    extraction = ExtractionResult(
                        status="unsupported",
                        reason="extension_disabled"
                        if extension in SUPPORTED_EXTENSIONS
                        else "unsupported_extension",
                    )
                else:
                    connection.commit()
                    # Reconciliation verifies bytes even with unchanged timestamps;
                    # identical content reuses extraction across copies and renames.
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            if cancelled():
                                raise InterruptedError("Indexlauf abgebrochen.")
                            digest.update(chunk)
                    content_hash = digest.hexdigest()
                    root_path = (
                        metadata.project_root.source.relative_path if metadata.project_root else ""
                    )
                    forced = force_extraction and (
                        selected_projects is None or root_ids.get(root_path) in selected_projects
                    )
                    extraction = (
                        None
                        if forced
                        else self.extraction_store.get(
                            content_hash,
                            version_hash,
                            max_attempts=getattr(settings, "extraction_retry_attempts", 3),
                        )
                    )
                    if extraction is None:
                        if hasattr(self.extractor, "extract_document"):
                            extraction = self.extractor.extract_document(path, settings, cancelled)
                        else:
                            content = self.extractor.extract(path, settings, cancelled)
                            extraction = ExtractionResult(
                                text=content,
                                blocks=(ExtractionBlock(content),) if content else (),
                                status="ok" if content else "no_text",
                            )
                        after = path.stat()
                        if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                            extraction = ExtractionResult(
                                status="error", reason="source_changed_during_extraction"
                            )
                        extraction = replace(
                            extraction, content_hash=content_hash, version_hash=version_hash
                        )
                        extraction = replace(
                            extraction, artifact_hash=artifact_identity(extraction)
                        )
                        if not self.extraction_store.put(
                            extraction,
                            retry_delay_seconds=getattr(
                                settings, "extraction_retry_delay_seconds", 300
                            ),
                            max_store_mb=getattr(settings, "extraction_store_max_mb", 1024),
                        ):
                            extraction = replace(
                                extraction, status="partial", reason="artifact_store_budget"
                            )
                        if cancelled():
                            raise InterruptedError("Indexlauf abgebrochen.")
                extraction = replace(
                    extraction, content_hash=content_hash, version_hash=version_hash
                )
                self._writer.upsert_file(
                    connection,
                    source_id=source_id,
                    relative_path=relative,
                    path=path,
                    stat=stat,
                    content=extraction.text,
                    eligible=eligible,
                    metadata=metadata,
                    extraction=extraction,
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
        self._cleanup_extractions(settings)
        return self.active_path

    def _cleanup_extractions(self, settings: ServerSettings) -> None:
        """Protect active, backup and published catalog references before pruning."""
        protected: set[tuple[str, str]] = set()
        try:
            for path in self.catalog_root.rglob("*.db"):
                protected.update(self._artifact_references(path))
            archives = self.data_path / "generations-v2" / "index" / "archives"
            with TemporaryDirectory(prefix="papagui-cache-retention-") as temporary:
                for archive in archives.glob("*.zip"):
                    with zipfile.ZipFile(archive) as bundle:
                        if "index/catalog/active.db" not in bundle.namelist():
                            continue
                        # Copy a fixed member name; archive names never become paths.
                        path = Path(temporary) / "catalog.db"
                        with (
                            bundle.open("index/catalog/active.db") as source,
                            path.open("wb") as destination,
                        ):
                            shutil.copyfileobj(source, destination)
                        protected.update(self._artifact_references(path))
            self.extraction_store.cleanup(
                protected=protected,
                retention_days=getattr(settings, "extraction_retention_days", 30),
            )
        except (sqlite3.Error, OSError, zipfile.BadZipFile):
            # Unreadable retained snapshots may have live refs. Skipping maintenance
            # cannot invalidate an already activated catalog or delete those refs.
            return

    @staticmethod
    def _artifact_references(path: Path) -> set[tuple[str, str]]:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(files)")}
            if "extraction_version" not in columns:
                return set()
            return {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT content_hash, extraction_version FROM files WHERE content_hash<>''"
                )
            }
        finally:
            connection.close()

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
            for source, destination in zip(reversed(backups[:-1]), reversed(backups[1:])):
                if source.exists():
                    os.replace(source, destination)
            os.replace(previous, backups[0])

    def _resume_matches(self, source_path: Path, source_id: str, settings_fingerprint: str) -> bool:
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
        return self._scanner.files(root, excluded, newest_years_first=newest_years_first)

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
            return (
                row is not None
                and str(row[0]) == CATALOG_SCHEMA_VERSION
                and {"source_id", "relative_path"}.issubset(columns)
            )
        except sqlite3.Error:
            return False
        finally:
            if "connection" in locals():
                connection.close()


def _source_uri(source_id: str, relative_path: str) -> str:
    return f"source://{source_id}/{relative_path}"
