"""Publish and consume immutable, checksummed PapaGUI data generations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from tempfile import TemporaryDirectory
import urllib.error
import urllib.request
import uuid
import zipfile


GENERATION_BACKUP_COUNT = 3
_PUBLICATION_LOCK = threading.RLock()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_db = sqlite3.connect(destination)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()


@dataclass(frozen=True)
class PublishedGeneration:
    generation: str
    archive: Path
    manifest: Path


class IndexGenerationPublisher:
    """Create immutable server generations and retain three predecessors."""

    def __init__(self, data_path: Path, backup_count: int = GENERATION_BACKUP_COUNT):
        self.data_path = data_path.resolve()
        self.root = self.data_path / "publications"
        self.generations = self.root / "generations"
        self.current_manifest = self.root / "current.json"
        self.backup_count = backup_count

    def publish(self) -> PublishedGeneration:
        with _PUBLICATION_LOCK:
            return self._publish_locked()

    def _publish_locked(self) -> PublishedGeneration:
        generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.generations.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self.root, prefix=".publish-") as directory:
            staging = Path(directory) / generation
            self._snapshot_payload(staging)
            files = self._file_manifest(staging)
            payload_manifest = {
                "schema_version": 1,
                "generation": generation,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": files,
            }
            _atomic_json(staging / "manifest.json", payload_manifest)
            archive = self.generations / f"{generation}.zip"
            temporary_archive = archive.with_name(f".{archive.name}.tmp")
            with zipfile.ZipFile(
                temporary_archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as bundle:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(staging))
            os.replace(temporary_archive, archive)

        public_manifest = {
            "schema_version": 1,
            "generation": generation,
            "created_at": payload_manifest["created_at"],
            "archive": archive.name,
            "size": archive.stat().st_size,
            "sha256": _sha256(archive),
        }
        manifest_path = self.generations / f"{generation}.json"
        _atomic_json(manifest_path, public_manifest)
        _atomic_json(self.current_manifest, public_manifest)
        self._prune()
        return PublishedGeneration(generation, archive, manifest_path)

    def _snapshot_payload(self, destination: Path) -> None:
        sources = [self.data_path / "customers.db"]
        index_root = self.data_path / "index"
        sources.extend(
            path
            for path in index_root.rglob("*.db")
            if not any(part in {"builds", "backups", "corrupt"} for part in path.parts)
        )
        for source in sources:
            if not source.exists():
                continue
            relative = source.relative_to(self.data_path)
            _snapshot_sqlite(source, destination / relative)

    @staticmethod
    def _file_manifest(root: Path) -> list[dict[str, object]]:
        return [
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]

    def _prune(self) -> None:
        keep = self.backup_count + 1
        archives = sorted(self.generations.glob("*.zip"), reverse=True)
        for archive in archives[keep:]:
            archive.unlink(missing_ok=True)
            archive.with_suffix(".json").unlink(missing_ok=True)


class IndexSyncError(RuntimeError):
    """A remote generation was unavailable or failed integrity validation."""


class IndexGenerationClient:
    """Download, verify and atomically activate server generations locally."""

    def __init__(
        self,
        server_url: str,
        client_root: Path,
        token: str = "",
        backup_count: int = GENERATION_BACKUP_COUNT,
        timeout_seconds: float = 15,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.client_root = client_root.resolve()
        self.generations = self.client_root / "generations"
        self.current_link = self.client_root / "current"
        self.token = token
        self.backup_count = backup_count
        self.timeout_seconds = timeout_seconds

    def sync(self) -> bool:
        remote = self._get_json("/v1/index/current")
        generation = str(remote["generation"])
        if self._current_generation() == generation:
            return False
        archive_name = str(remote["archive"])
        self.generations.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self.client_root, prefix=".sync-") as directory:
            temporary = Path(directory)
            archive = temporary / archive_name
            self._download(f"/v1/index/generations/{archive_name}", archive)
            if archive.stat().st_size != int(remote["size"]):
                raise IndexSyncError("Die Größe der Indexgeneration stimmt nicht.")
            if _sha256(archive) != str(remote["sha256"]):
                raise IndexSyncError("Die Prüfsumme der Indexgeneration stimmt nicht.")
            extracted = temporary / "payload"
            with zipfile.ZipFile(archive) as bundle:
                self._safe_extract(bundle, extracted)
            self._validate_payload(extracted, generation)
            destination = self.generations / generation
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(extracted, destination)
        self._activate(generation)
        self._prune()
        return True

    def has_local_generation(self) -> bool:
        return self.current_link.is_symlink() and self.current_link.resolve().is_dir()

    def bootstrap_from(self, data_path: Path) -> bool:
        """Seed the offline cache once from a pre-distribution local data folder."""
        if self.has_local_generation():
            return False
        data_path = data_path.resolve()
        if not (data_path / "index").is_dir():
            return False
        generation = "bootstrap-local"
        destination = self.generations / generation
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(data_path / "index", destination / "index", dirs_exist_ok=True)
        if (data_path / "customers.db").is_file():
            _snapshot_sqlite(data_path / "customers.db", destination / "customers.db")
        self._activate(generation)
        return True

    def _request(self, path: str) -> urllib.request.Request:
        request = urllib.request.Request(f"{self.server_url}{path}")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        return request

    def _get_json(self, path: str) -> dict[str, object]:
        try:
            with urllib.request.urlopen(
                self._request(path), timeout=self.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise IndexSyncError(f"Indexserver nicht erreichbar: {exc}") from exc

    def _download(self, path: str, destination: Path) -> None:
        try:
            with urllib.request.urlopen(
                self._request(path), timeout=self.timeout_seconds
            ) as response, destination.open("wb") as target:
                shutil.copyfileobj(response, target)
        except (OSError, urllib.error.URLError) as exc:
            raise IndexSyncError(f"Indexgeneration konnte nicht geladen werden: {exc}") from exc

    @staticmethod
    def _safe_extract(bundle: zipfile.ZipFile, destination: Path) -> None:
        destination.mkdir(parents=True)
        root = destination.resolve()
        for entry in bundle.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(root):
                raise IndexSyncError("Unsicherer Pfad in der Indexgeneration.")
        bundle.extractall(destination)

    @staticmethod
    def _validate_payload(root: Path, generation: str) -> None:
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IndexSyncError("Das Generationsmanifest ist ungültig.") from exc
        if str(manifest.get("generation")) != generation:
            raise IndexSyncError("Die Generationskennung stimmt nicht.")
        for entry in manifest.get("files", []):
            path = root / str(entry["path"])
            if not path.is_file() or path.stat().st_size != int(entry["size"]):
                raise IndexSyncError(f"Indexdatei fehlt oder ist unvollständig: {path.name}")
            if _sha256(path) != str(entry["sha256"]):
                raise IndexSyncError(f"Indexdatei ist beschädigt: {path.name}")

    def _activate(self, generation: str) -> None:
        temporary_link = self.client_root / f".current-{uuid.uuid4().hex}"
        temporary_link.symlink_to(Path("generations") / generation, target_is_directory=True)
        os.replace(temporary_link, self.current_link)

    def _current_generation(self) -> str:
        if not self.current_link.is_symlink():
            return ""
        try:
            return self.current_link.resolve().name
        except OSError:
            return ""

    def _prune(self) -> None:
        keep = self.backup_count + 1
        entries = sorted(
            (path for path in self.generations.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
        for path in entries[keep:]:
            shutil.rmtree(path)
