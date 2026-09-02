"""Immutable, checksummed generation-v2 storage."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import threading
from typing import Any
import uuid
import zipfile

from papagui_contracts.generations import GenerationComponentKind

from papagui_server.domain.errors import ResourceNotFoundError


GENERATION_SCHEMA_VERSION = 2
BACKUP_COUNT = 3
logger = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


class GenerationV2Publisher:
    """Publish index and customers as independently rotating components."""

    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path.resolve()
        self.root = self.data_path / "generations-v2"
        self.active_path = self.data_path / "active-generation.json"
        self.retention_status_path = self.root / "retention-status.json"
        self._lock = threading.RLock()
        self._retention_failures = self._load_retention_failures()

    def publish_index(self) -> dict[str, Any]:
        return self._publish(
            GenerationComponentKind.INDEX.value, self._index_sources()
        )

    def _index_sources(self) -> list[tuple[Path, Path]]:
        sources: list[tuple[Path, Path]] = []
        index_root = self.data_path / "index"
        if not index_root.is_dir():
            raise ResourceNotFoundError("Es ist noch kein Index vorhanden.")
        for source in sorted(index_root.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(index_root)
            if any(part in {"builds", "backups", "corrupt", "jobs"} for part in relative.parts):
                continue
            if source.suffix.casefold() not in {".db", ".json"}:
                continue
            sources.append((source, Path("index") / relative))
        if not sources:
            raise ResourceNotFoundError("Es ist noch kein veröffentlichbarer Index vorhanden.")
        return sources

    def publish_customers(self) -> dict[str, Any]:
        database = self.data_path / "customers.db"
        if not database.is_file():
            raise ResourceNotFoundError("Es ist noch keine Kundendatenbank vorhanden.")
        return self._publish(
            GenerationComponentKind.CUSTOMERS.value,
            [(database, Path("customers.db"))],
        )

    def publish_all(self) -> dict[str, Any]:
        database = self.data_path / "customers.db"
        if not database.is_file():
            raise ResourceNotFoundError("Es ist noch keine Kundendatenbank vorhanden.")
        staged: dict[str, dict[str, Any]] = {}
        with self._lock:
            for component in (
                GenerationComponentKind.INDEX.value,
                GenerationComponentKind.CUSTOMERS.value,
            ):
                self._best_effort_prune(component)
            try:
                staged[GenerationComponentKind.INDEX.value] = self._stage(
                    GenerationComponentKind.INDEX.value, self._index_sources()
                )
                staged[GenerationComponentKind.CUSTOMERS.value] = self._stage(
                    GenerationComponentKind.CUSTOMERS.value,
                    [(database, Path("customers.db"))],
                )
                active = self._activate(staged)
            except Exception:
                self._discard(staged.values())
                raise
            for component in staged:
                self._best_effort_prune(component)
            return active

    def current(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != GENERATION_SCHEMA_VERSION
        ):
            return None
        return payload

    def retention_status(self) -> dict[str, Any]:
        """Return durable cleanup diagnostics without weakening active data."""
        with self._lock:
            failures = dict(self._retention_failures)
        return {
            "state": "degraded" if failures else "ok",
            "required_predecessors": BACKUP_COUNT,
            "failures": failures,
        }

    def descriptor(self, component: str, generation: str) -> dict[str, Any]:
        component = self._component(component)
        path = self._component_root(component) / "manifests" / f"{self._generation(generation)}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ResourceNotFoundError("Generation nicht gefunden.") from error

    def archive_path(self, component: str, generation: str) -> Path:
        component = self._component(component)
        archive = self._component_root(component) / "archives" / f"{self._generation(generation)}.zip"
        if not archive.is_file():
            raise ResourceNotFoundError("Generation nicht gefunden.")
        descriptor = self.descriptor(component, generation)
        if archive.stat().st_size != int(descriptor["size"]):
            raise ResourceNotFoundError("Die Generation ist unvollständig.")
        if sha256_file(archive) != str(descriptor["sha256"]):
            raise ResourceNotFoundError("Die Generation ist beschädigt.")
        return archive

    def delete_index_generations(self) -> None:
        with self._lock:
            index_root = self._component_root(GenerationComponentKind.INDEX.value)
            if index_root.exists():
                shutil.rmtree(index_root)
            current = self.current() or {}
            components = dict(current.get("components") or {})
            customers = components.get("customers")
            if customers:
                atomic_json(
                    self.active_path,
                    {
                        "schema_version": GENERATION_SCHEMA_VERSION,
                        "created_at": _utc_now(),
                        "components": {"index": None, "customers": customers},
                        "legacy_combined": False,
                    },
                )
            else:
                self.active_path.unlink(missing_ok=True)

    def _publish(
        self, component: str, sources: list[tuple[Path, Path]]
    ) -> dict[str, Any]:
        component = self._component(component)
        with self._lock:
            self._best_effort_prune(component)
            descriptor = self._stage(component, sources)
            try:
                self._activate({component: descriptor})
            except Exception:
                self._discard([descriptor])
                raise
            self._best_effort_prune(component)
            return descriptor

    def _stage(
        self, component: str, sources: list[tuple[Path, Path]]
    ) -> dict[str, Any]:
        component = self._component(component)
        generation = _generation_id()
        component_root = self._component_root(component)
        archives = component_root / "archives"
        manifests = component_root / "manifests"
        archives.mkdir(parents=True, exist_ok=True)
        manifests.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=component_root, prefix=".build-") as temp:
            payload_root = Path(temp) / "payload"
            payload_root.mkdir()
            for source, relative in sources:
                destination = payload_root / relative
                if source.suffix.casefold() == ".db":
                    snapshot_sqlite(source, destination)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            file_entries = [
                {
                    "path": path.relative_to(payload_root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in sorted(payload_root.rglob("*"))
                if path.is_file()
            ]
            inner_manifest = {
                "schema_version": GENERATION_SCHEMA_VERSION,
                "component": component,
                "generation": generation,
                "created_at": _utc_now(),
                "files": file_entries,
            }
            atomic_json(payload_root / "manifest.json", inner_manifest)
            archive = archives / f"{generation}.zip"
            temporary_archive = archives / f".{generation}.{uuid.uuid4().hex}.tmp"
            try:
                with zipfile.ZipFile(
                    temporary_archive, "w", compression=zipfile.ZIP_DEFLATED
                ) as bundle:
                    for path in sorted(payload_root.rglob("*")):
                        if path.is_file():
                            bundle.write(path, path.relative_to(payload_root))
                os.replace(temporary_archive, archive)
            finally:
                temporary_archive.unlink(missing_ok=True)
        descriptor = {
            "kind": component,
            "generation": generation,
            "created_at": inner_manifest["created_at"],
            "archive": f"v2/generations/{component}/{generation}/archive",
            "size": archive.stat().st_size,
            "sha256": sha256_file(archive),
            "content_type": "application/zip",
        }
        atomic_json(manifests / f"{generation}.json", descriptor)
        return descriptor

    def _activate(
        self, staged: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        previous = self.current() or {}
        components = dict(previous.get("components") or {})
        components.update(staged)
        active = {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "created_at": _utc_now(),
            "components": {
                "index": components.get("index"),
                "customers": components.get("customers"),
            },
            "legacy_combined": False,
        }
        pointer_backups: dict[str, dict[str, Any] | None] = {}
        try:
            for component, descriptor in staged.items():
                pointer = self._component_root(component) / "active-generation.json"
                try:
                    value = json.loads(pointer.read_text(encoding="utf-8"))
                    pointer_backups[component] = value if isinstance(value, dict) else None
                except (OSError, TypeError, ValueError):
                    pointer_backups[component] = None
                atomic_json(pointer, descriptor)
            # This single replace is the only externally authoritative switch.
            atomic_json(self.active_path, active)
        except Exception:
            for component, payload in pointer_backups.items():
                pointer = self._component_root(component) / "active-generation.json"
                if payload is None:
                    pointer.unlink(missing_ok=True)
                else:
                    atomic_json(pointer, payload)
            raise
        return active

    def _discard(self, descriptors: Any) -> None:
        for descriptor in descriptors:
            component = str(descriptor.get("kind", ""))
            generation = str(descriptor.get("generation", ""))
            if component not in {"index", "customers"} or not generation:
                continue
            root = self._component_root(component)
            (root / "archives" / f"{generation}.zip").unlink(missing_ok=True)
            (root / "manifests" / f"{generation}.json").unlink(missing_ok=True)

    def _prune(self, component: str) -> None:
        root = self._component_root(component)
        current = self.current() or {}
        active = str(
            ((current.get("components") or {}).get(component) or {}).get(
                "generation", ""
            )
        )
        # Without an authoritative active pointer deletion would be unsafe.
        if not active:
            return
        archives = root / "archives"
        manifests = root / "manifests"
        generations = {
            path.stem for path in archives.glob("*.zip")
        } | {path.stem for path in manifests.glob("*.json")}
        predecessors = sorted(
            (generation for generation in generations if generation < active),
            reverse=True,
        )
        keep = {active, *predecessors[:BACKUP_COUNT]}
        for generation in sorted(generations - keep):
            manifests.joinpath(f"{generation}.json").unlink(missing_ok=True)
            archives.joinpath(f"{generation}.zip").unlink(missing_ok=True)

    def _best_effort_prune(self, component: str) -> None:
        try:
            self._prune(component)
        except Exception as error:  # Active data wins over strict cleanup.
            self._retention_failures[component] = {
                "message": str(error) or type(error).__name__,
                "observed_at": _utc_now(),
            }
            logger.exception(
                "Generationsbereinigung für %s fehlgeschlagen; "
                "sie wird beim nächsten Publizieren erneut versucht",
                component,
            )
            self._persist_retention_failures()
        else:
            if self._retention_failures.pop(component, None) is not None:
                self._persist_retention_failures()

    def _load_retention_failures(self) -> dict[str, dict[str, str]]:
        try:
            payload = json.loads(self.retention_status_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        failures = payload.get("failures") if isinstance(payload, dict) else None
        if not isinstance(failures, dict):
            return {}
        return {
            component: {
                "message": str(value.get("message", "")),
                "observed_at": str(value.get("observed_at", "")),
            }
            for component, value in failures.items()
            if component in {"index", "customers"} and isinstance(value, dict)
        }

    def _persist_retention_failures(self) -> None:
        try:
            atomic_json(
                self.retention_status_path,
                {
                    "state": "degraded" if self._retention_failures else "ok",
                    "required_predecessors": BACKUP_COUNT,
                    "failures": self._retention_failures,
                    "updated_at": _utc_now(),
                },
            )
        except Exception:
            # Preserve the in-memory diagnosis even if the same filesystem
            # problem prevents persisting it.
            logger.exception("Retention-Diagnose konnte nicht gespeichert werden")

    def _component_root(self, component: str) -> Path:
        return self.root / self._component(component)

    @staticmethod
    def _component(component: str) -> str:
        allowed = {
            GenerationComponentKind.INDEX.value,
            GenerationComponentKind.CUSTOMERS.value,
        }
        if component not in allowed:
            raise ResourceNotFoundError("Unbekannte Generationskomponente.")
        return component

    @staticmethod
    def _generation(generation: str) -> str:
        if not generation or any(character not in "0123456789TZ.-abcdef" for character in generation):
            raise ResourceNotFoundError("Ungültige Generationskennung.")
        return generation


def _generation_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{now}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
