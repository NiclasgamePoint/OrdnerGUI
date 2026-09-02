"""Cross-platform immutable generation storage without filesystem symlinks."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
from typing import Iterable, Mapping
import uuid
import warnings
import zipfile

from papagui_contracts.generations import (
    GenerationComponentKind,
    GenerationComponentManifest,
)

from papagui_client.application.models import InstalledGeneration


ACTIVE_POINTER_SCHEMA = 2
DEFAULT_BACKUP_COUNT = 3


class GenerationIntegrityError(RuntimeError):
    pass


class GenerationRetentionWarning(RuntimeWarning):
    """A valid active generation is safe, but stale backups remain on disk."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FilesystemGenerationStore:
    def __init__(self, root: Path, backup_count: int = DEFAULT_BACKUP_COUNT):
        if backup_count != DEFAULT_BACKUP_COUNT:
            raise ValueError("PapaGUI clients retain exactly three predecessor generations")
        self._root = root.expanduser().resolve()
        self._generations = self._root / "generations"
        self._pointer = self._root / "active-generation.json"
        self._legacy_link = self._root / "current"
        self._backup_count = backup_count
        self._lock = threading.RLock()
        self._retention_warning: str | None = None

    @property
    def root(self) -> Path:
        return self._root

    @property
    def retention_warning(self) -> str | None:
        """Describe the last failed cleanup without invalidating active data."""
        return self._retention_warning

    def current_generation(self, kind: GenerationComponentKind) -> str | None:
        entry = self._component_entry(kind)
        return str(entry.get("generation")) if entry is not None else None

    def active_component_path(self, kind: GenerationComponentKind) -> Path | None:
        entry = self._component_entry(kind)
        if entry is None:
            return None
        relative = Path(str(entry.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise GenerationIntegrityError("active generation pointer escapes the client cache")
        target = (self._root / relative).resolve()
        if not target.is_relative_to(self._root) or not target.is_dir():
            return None
        return target

    def import_legacy_symlink(self) -> bool:
        """Convert the old `current` symlink into a portable JSON pointer once."""
        with self._lock:
            if self._pointer.exists() or not self._legacy_link.is_symlink():
                return False
            try:
                target = self._legacy_link.resolve(strict=True)
            except OSError:
                return False
            if not target.is_dir() or not target.is_relative_to(self._root):
                return False
            relative = target.relative_to(self._root).as_posix()
            generation = target.name
            components: dict[str, object] = {}
            if (target / "index").is_dir():
                components[GenerationComponentKind.INDEX.value] = {
                    "generation": generation,
                    "path": (target / "index").relative_to(self._root).as_posix(),
                    "legacy_combined": True,
                }
            elif (target / "catalog").is_dir():
                components[GenerationComponentKind.INDEX.value] = {
                    "generation": generation,
                    "path": relative,
                    "legacy_combined": True,
                }
            if (target / "customers.db").is_file():
                components[GenerationComponentKind.CUSTOMERS.value] = {
                    "generation": generation,
                    "path": relative,
                    "legacy_combined": True,
                }
            if not components:
                return False
            history = {
                name: [str(entry["generation"])]
                for name, entry in components.items()
                if isinstance(entry, dict)
            }
            self._write_pointer(
                {
                    "schema_version": ACTIVE_POINTER_SCHEMA,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "components": components,
                    "history": history,
                }
            )
            return True

    def install_archive(
        self,
        kind: GenerationComponentKind,
        component: GenerationComponentManifest,
        archive: Path,
        *,
        legacy_combined: bool = False,
    ) -> InstalledGeneration:
        self._validate_archive_file(component, archive)
        destination = self._generations / kind.value / component.generation
        if destination.exists():
            if self.current_generation(kind) == component.generation:
                return InstalledGeneration(kind.value, component.generation, self._relative(destination), False)
            raise GenerationIntegrityError(
                f"inactive generation already exists and will not be overwritten: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.parent / f".{component.generation}.{uuid.uuid4().hex}.staging"
        try:
            staging.mkdir()
            with zipfile.ZipFile(archive) as bundle:
                self._safe_extract(bundle, staging)
            payload = self._payload_root(staging)
            self._validate_internal_manifest(
                payload,
                component,
                expected_schema=1 if legacy_combined else 2,
                expected_kind=None if legacy_combined else kind,
            )
            if payload == staging:
                os.replace(staging, destination)
            else:
                os.replace(payload, destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return InstalledGeneration(kind.value, component.generation, self._relative(destination), True)

    def activate(self, installed: Iterable[InstalledGeneration]) -> Mapping[str, str]:
        items = tuple(installed)
        with self._lock:
            payload = self._read_pointer() or {
                "schema_version": ACTIVE_POINTER_SCHEMA,
                "components": {},
                "history": {},
            }
            components = dict(payload.get("components") or {})
            history = {
                str(name): [str(value) for value in values]
                for name, values in dict(payload.get("history") or {}).items()
            }
            for item in items:
                previous = components.get(item.component)
                previous_generation = (
                    str(previous.get("generation")) if isinstance(previous, dict) else None
                )
                generations = [item.generation]
                if previous_generation:
                    generations.append(previous_generation)
                generations.extend(history.get(item.component, []))
                history[item.component] = list(dict.fromkeys(generations))[: self._backup_count + 1]
                components[item.component] = {
                    "generation": item.generation,
                    "path": item.path,
                }
            pointer = {
                "schema_version": ACTIVE_POINTER_SCHEMA,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "components": components,
                "history": history,
            }
            self._write_pointer(pointer)
            self._prune_best_effort(history)
            return {
                name: str(value["generation"])
                for name, value in components.items()
                if isinstance(value, dict) and "generation" in value
            }

    def retry_retention(self) -> bool:
        """Retry cleanup explicitly while keeping the active pointer untouched."""
        with self._lock:
            payload = self._read_pointer()
            if payload is None:
                self._retention_warning = None
                return True
            history = {
                str(name): [str(value) for value in values]
                for name, values in dict(payload.get("history") or {}).items()
            }
            return self._prune_best_effort(history)

    def discard(self, installed: Iterable[InstalledGeneration]) -> None:
        for item in installed:
            if not item.created:
                continue
            target = (self._root / item.path).resolve()
            expected_parent = (self._generations / item.component).resolve()
            if target.parent == expected_parent and target.is_dir():
                shutil.rmtree(target)

    def _component_entry(self, kind: GenerationComponentKind) -> dict[str, object] | None:
        payload = self._read_pointer()
        if payload is None:
            return None
        entry = dict(payload.get("components") or {}).get(kind.value)
        return dict(entry) if isinstance(entry, dict) else None

    def _read_pointer(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self._pointer.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise GenerationIntegrityError("active-generation.json is invalid") from exc
        if payload.get("schema_version") != ACTIVE_POINTER_SCHEMA:
            raise GenerationIntegrityError("unsupported active generation pointer")
        return payload

    def _write_pointer(self, payload: Mapping[str, object]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = self._root / f".{self._pointer.name}.{uuid.uuid4().hex}.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._pointer)

    @staticmethod
    def _validate_archive_file(component: GenerationComponentManifest, archive: Path) -> None:
        if not archive.is_file() or archive.stat().st_size != component.size:
            raise GenerationIntegrityError("generation archive size mismatch")
        if sha256_file(archive) != component.sha256:
            raise GenerationIntegrityError("generation archive checksum mismatch")
        if not zipfile.is_zipfile(archive):
            raise GenerationIntegrityError("generation archive is not a ZIP file")

    @staticmethod
    def _safe_extract(bundle: zipfile.ZipFile, destination: Path) -> None:
        root = destination.resolve()
        for entry in bundle.infolist():
            path = Path(entry.filename)
            target = (destination / path).resolve()
            mode = entry.external_attr >> 16
            if (
                path.is_absolute()
                or ".." in path.parts
                or not target.is_relative_to(root)
                or stat.S_ISLNK(mode)
            ):
                raise GenerationIntegrityError("unsafe path in generation archive")
        invalid = bundle.testzip()
        if invalid is not None:
            raise GenerationIntegrityError(f"corrupt ZIP member: {invalid}")
        bundle.extractall(destination)

    @staticmethod
    def _payload_root(staging: Path) -> Path:
        entries = list(staging.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return staging

    @staticmethod
    def _validate_internal_manifest(
        payload: Path,
        component: GenerationComponentManifest,
        *,
        expected_schema: int,
        expected_kind: GenerationComponentKind | None,
    ) -> None:
        manifest_path = payload / "manifest.json"
        if not manifest_path.exists():
            raise GenerationIntegrityError("component archive has no internal manifest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GenerationIntegrityError("invalid component manifest") from exc
        if manifest.get("schema_version") != expected_schema:
            raise GenerationIntegrityError("component manifest schema mismatch")
        if expected_kind is not None and manifest.get("component") != expected_kind.value:
            raise GenerationIntegrityError("component manifest kind mismatch")
        declared_generation = manifest.get("generation")
        if declared_generation is not None and str(declared_generation) != component.generation:
            raise GenerationIntegrityError("component generation mismatch")
        files = manifest.get("files", ())
        if not isinstance(files, list):
            raise GenerationIntegrityError("invalid component file list")
        for entry in files:
            if not isinstance(entry, dict):
                raise GenerationIntegrityError("invalid component file entry")
            relative = Path(str(entry.get("path", "")))
            target = (payload / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not target.is_relative_to(payload.resolve())
                or not target.is_file()
            ):
                raise GenerationIntegrityError("component manifest references an unsafe or missing file")
            if "size" in entry and target.stat().st_size != int(entry["size"]):
                raise GenerationIntegrityError(f"component file size mismatch: {relative}")
            if "sha256" in entry and sha256_file(target) != str(entry["sha256"]):
                raise GenerationIntegrityError(f"component file checksum mismatch: {relative}")

    def _prune(self, history: Mapping[str, list[str]]) -> None:
        for component, generations in history.items():
            root = self._generations / component
            if not root.is_dir():
                continue
            keep = set(generations[: self._backup_count + 1])
            for candidate in root.iterdir():
                if candidate.is_dir() and candidate.name not in keep:
                    shutil.rmtree(candidate)

    def _prune_best_effort(self, history: Mapping[str, list[str]]) -> bool:
        error: OSError | None = None
        for _attempt in range(2):
            try:
                self._prune(history)
            except OSError as exc:
                error = exc
            else:
                self._retention_warning = None
                return True
        message = (
            "Alte Clientgenerationen konnten nach zwei Versuchen nicht bereinigt "
            f"werden; der aktive Stand bleibt gültig: {error}"
        )
        self._retention_warning = message
        warnings.warn(message, GenerationRetentionWarning, stacklevel=2)
        return False

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()
