from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import zipfile

import pytest

from papagui_contracts import GenerationComponentKind, GenerationComponentManifest
from papagui_client.adapters.filesystem_generations import (
    FilesystemGenerationStore,
    GenerationIntegrityError,
    GenerationRetentionWarning,
)
from papagui_client.application.models import InstalledGeneration


NOW = "2026-09-02T10:00:00+00:00"


def descriptor(path: Path, generation="g-1"):
    return GenerationComponentManifest(
        GenerationComponentKind.INDEX,
        generation,
        NOW,
        path.name,
        path.stat().st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def bundle(tmp_path: Path, files, generation="g-1", *, include_manifest=True):
    path = tmp_path / f"{generation}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        if include_manifest and "manifest.json" not in files:
            entries = [
                {
                    "path": name,
                    "size": len(value.encode() if isinstance(value, str) else value),
                    "sha256": hashlib.sha256(
                        value.encode() if isinstance(value, str) else value
                    ).hexdigest(),
                }
                for name, value in files.items()
            ]
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": 2,
                        "component": "index",
                        "generation": generation,
                        "files": entries,
                    }
                ),
            )
    return path


def test_store_rejects_backup_override_and_invalid_pointers(tmp_path):
    with pytest.raises(ValueError):
        FilesystemGenerationStore(tmp_path, backup_count=2)
    store = FilesystemGenerationStore(tmp_path / "cache")
    assert store.current_generation(GenerationComponentKind.INDEX) is None
    assert store.active_component_path(GenerationComponentKind.INDEX) is None
    store.root.mkdir()
    pointer = store.root / "active-generation.json"
    pointer.write_text("broken", encoding="utf-8")
    with pytest.raises(GenerationIntegrityError, match="invalid"):
        store.current_generation(GenerationComponentKind.INDEX)
    pointer.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(GenerationIntegrityError, match="unsupported"):
        store.current_generation(GenerationComponentKind.INDEX)
    pointer.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "components": {"index": {"generation": "g", "path": "../escape"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GenerationIntegrityError, match="escapes"):
        store.active_component_path(GenerationComponentKind.INDEX)
    pointer.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "components": {"index": {"generation": "g", "path": "missing"}},
            }
        ),
        encoding="utf-8",
    )
    assert store.active_component_path(GenerationComponentKind.INDEX) is None


def test_legacy_symlink_rejection_and_catalog_shape(tmp_path):
    store = FilesystemGenerationStore(tmp_path / "cache")
    assert not store.import_legacy_symlink()
    store.root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "current").symlink_to(outside, target_is_directory=True)
    assert not store.import_legacy_symlink()
    (store.root / "current").unlink()
    empty = store.root / "empty"
    empty.mkdir()
    (store.root / "current").symlink_to(empty, target_is_directory=True)
    assert not store.import_legacy_symlink()
    (store.root / "current").unlink()
    catalog = store.root / "generations" / "legacy"
    (catalog / "catalog").mkdir(parents=True)
    (store.root / "current").symlink_to(
        Path("generations") / "legacy", target_is_directory=True
    )
    assert store.import_legacy_symlink()
    assert store.active_component_path(GenerationComponentKind.INDEX) == catalog
    assert not store.import_legacy_symlink()


def test_archive_file_integrity_and_unsafe_members(tmp_path):
    store = FilesystemGenerationStore(tmp_path / "cache")
    valid = bundle(tmp_path, {"value.txt": "ok"})
    component = descriptor(valid)
    missing = tmp_path / "missing.zip"
    with pytest.raises(GenerationIntegrityError, match="size"):
        store.install_archive(GenerationComponentKind.INDEX, component, missing)
    wrong_size = GenerationComponentManifest(
        component.kind,
        component.generation,
        component.created_at,
        component.archive,
        component.size + 1,
        component.sha256,
    )
    with pytest.raises(GenerationIntegrityError, match="size"):
        store.install_archive(GenerationComponentKind.INDEX, wrong_size, valid)
    wrong_hash = GenerationComponentManifest(
        component.kind,
        component.generation,
        component.created_at,
        component.archive,
        component.size,
        "0" * 64,
    )
    with pytest.raises(GenerationIntegrityError, match="checksum"):
        store.install_archive(GenerationComponentKind.INDEX, wrong_hash, valid)
    plain = tmp_path / "plain.zip"
    plain.write_bytes(b"not a zip")
    with pytest.raises(GenerationIntegrityError, match="not a ZIP"):
        store.install_archive(
            GenerationComponentKind.INDEX, descriptor(plain), plain
        )

    for name in ("../escape", "/absolute"):
        unsafe = bundle(
            tmp_path,
            {name: "bad"},
            generation=name.strip("./") + "x",
            include_manifest=False,
        )
        with pytest.raises(GenerationIntegrityError, match="unsafe"):
            store.install_archive(
                GenerationComponentKind.INDEX, descriptor(unsafe, unsafe.stem), unsafe
            )
    link = tmp_path / "link.zip"
    with zipfile.ZipFile(link, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = stat.S_IFLNK << 16
        archive.writestr(info, "target")
    with pytest.raises(GenerationIntegrityError, match="unsafe"):
        store.install_archive(
            GenerationComponentKind.INDEX, descriptor(link, "link"), link
        )


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("broken", "invalid component manifest"),
        (
            json.dumps(
                {"schema_version": 2, "component": "index", "generation": "other"}
            ),
            "generation mismatch",
        ),
        (
            json.dumps(
                {
                    "schema_version": 2,
                    "component": "index",
                    "generation": "g-1",
                    "files": {},
                }
            ),
            "file list",
        ),
        (
            json.dumps(
                {
                    "schema_version": 2,
                    "component": "index",
                    "generation": "g-1",
                    "files": ["bad"],
                }
            ),
            "file entry",
        ),
        (
            json.dumps(
                {
                    "schema_version": 2,
                    "component": "index",
                    "generation": "g-1",
                    "files": [{"path": "missing"}],
                }
            ),
            "unsafe or missing",
        ),
        (
            json.dumps(
                {
                    "generation": "g-1",
                    "schema_version": 2,
                    "component": "index",
                    "files": [{"path": "value.txt", "size": 99}],
                }
            ),
            "size mismatch",
        ),
        (
            json.dumps(
                {
                    "generation": "g-1",
                    "schema_version": 2,
                    "component": "index",
                    "files": [{"path": "value.txt", "sha256": "0" * 64}],
                }
            ),
            "checksum mismatch",
        ),
    ],
)
def test_internal_manifest_validation_and_staging_cleanup(tmp_path, manifest, message):
    archive = bundle(
        tmp_path,
        {"value.txt": "ok", "manifest.json": manifest},
        include_manifest=False,
    )
    store = FilesystemGenerationStore(tmp_path / "cache")
    with pytest.raises(GenerationIntegrityError, match=message):
        store.install_archive(
            GenerationComponentKind.INDEX, descriptor(archive), archive
        )
    assert not tuple((store.root / "generations").rglob("*.staging"))


def test_existing_install_discard_payload_roots_and_pruning(tmp_path):
    store = FilesystemGenerationStore(tmp_path / "cache")
    archive = bundle(tmp_path, {"single/value.txt": "ok"})
    component = descriptor(archive)
    installed = store.install_archive(GenerationComponentKind.INDEX, component, archive)
    store.activate([installed])
    same = store.install_archive(GenerationComponentKind.INDEX, component, archive)
    assert not same.created

    inactive_path = store.root / "generations" / "index" / "inactive"
    inactive_path.mkdir()
    inactive_archive = bundle(tmp_path, {"value": "x"}, generation="inactive")
    with pytest.raises(GenerationIntegrityError, match="will not be overwritten"):
        store.install_archive(
            GenerationComponentKind.INDEX,
            descriptor(inactive_archive, "inactive"),
            inactive_archive,
        )
    store.discard([same])
    assert installed.path
    created = inactive_path.parent / "discard"
    created.mkdir()
    store.discard(
        [InstalledGeneration("index", "discard", "generations/index/discard", True)]
    )
    assert not created.exists()

    for name in ("old-1", "old-2"):
        (inactive_path.parent / name).mkdir()
    store._prune({"index": ["g-1"]})
    assert not inactive_path.exists()
    assert (inactive_path.parent / "g-1").is_dir()


def test_v2_requires_internal_manifest_and_activation_survives_prune_error(tmp_path):
    store = FilesystemGenerationStore(tmp_path / "cache")
    missing_manifest = bundle(
        tmp_path, {"value.txt": "ok"}, generation="missing", include_manifest=False
    )
    with pytest.raises(GenerationIntegrityError, match="no internal manifest"):
        store.install_archive(
            GenerationComponentKind.INDEX,
            descriptor(missing_manifest, "missing"),
            missing_manifest,
        )

    archive = bundle(tmp_path, {"value.txt": "ok"}, generation="active")
    installed = store.install_archive(
        GenerationComponentKind.INDEX,
        descriptor(archive, "active"),
        archive,
    )
    original_prune = store._prune
    attempts = []

    def fail_prune(_history):
        attempts.append(True)
        raise OSError("busy")

    store._prune = fail_prune
    with pytest.warns(GenerationRetentionWarning, match="zwei Versuchen"):
        current = store.activate([installed])
    assert len(attempts) == 2
    assert "busy" in store.retention_warning
    store._prune = original_prune
    assert current["index"] == "active"
    assert store.active_component_path(GenerationComponentKind.INDEX).is_dir()
    assert store.retry_retention()
    assert store.retention_warning is None


def test_retention_retries_transient_os_error_without_warning(tmp_path):
    store = FilesystemGenerationStore(tmp_path / "cache")
    archive = bundle(tmp_path, {"value.txt": "ok"}, generation="active")
    installed = store.install_archive(
        GenerationComponentKind.INDEX,
        descriptor(archive, "active"),
        archive,
    )
    original_prune = store._prune
    attempts = []

    def flaky(history):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("temporarily busy")
        original_prune(history)

    store._prune = flaky
    store.activate([installed])
    assert len(attempts) == 2
    assert store.retention_warning is None
