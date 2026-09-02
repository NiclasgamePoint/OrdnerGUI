from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from papagui_contracts import (
    GenerationComponentKind,
    GenerationComponentManifest,
    GenerationManifest,
)
from papagui_client.adapters.filesystem_generations import FilesystemGenerationStore
from papagui_client.application.sync import SyncCoordinator, SyncError


NOW = "2026-09-02T10:00:00+00:00"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive(tmp_path: Path, kind: GenerationComponentKind, generation: str, value: str) -> Path:
    payload_file = "index/catalog/active.db" if kind is GenerationComponentKind.INDEX else "customers.db"
    content = value.encode()
    manifest = {
        "schema_version": 2,
        "component": kind.value,
        "generation": generation,
        "created_at": NOW,
        "files": [
            {
                "path": payload_file,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    target = tmp_path / f"{kind.value}-{generation}.zip"
    with zipfile.ZipFile(target, "w") as bundle:
        bundle.writestr(payload_file, content)
        bundle.writestr("manifest.json", json.dumps(manifest))
    return target


def component(kind: GenerationComponentKind, generation: str, path: Path):
    return GenerationComponentManifest(
        kind=kind,
        generation=generation,
        created_at=NOW,
        archive=f"v2/{kind.value}/{generation}.zip",
        size=path.stat().st_size,
        sha256=sha256(path),
    )


class FakeGateway:
    def __init__(self, manifest, files):
        self.manifest = manifest
        self.files = files

    def current_manifest(self):
        return self.manifest

    def download_component(self, kind, component_manifest, destination):
        shutil.copy2(self.files[(kind, component_manifest.generation)], destination)


def manifest(index, customers):
    return GenerationManifest(created_at=NOW, index=index, customers=customers)


def test_sync_activates_json_pointer_and_retains_active_plus_three(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    store = FilesystemGenerationStore(tmp_path / "cache")
    customer_archive = archive(source, GenerationComponentKind.CUSTOMERS, "customers-1", "customers")
    customer_component = component(GenerationComponentKind.CUSTOMERS, "customers-1", customer_archive)

    for number in range(1, 6):
        index_archive = archive(source, GenerationComponentKind.INDEX, f"index-{number}", str(number))
        index_component = component(GenerationComponentKind.INDEX, f"index-{number}", index_archive)
        gateway = FakeGateway(
            manifest(index_component, customer_component),
            {
                (GenerationComponentKind.INDEX, f"index-{number}"): index_archive,
                (GenerationComponentKind.CUSTOMERS, "customers-1"): customer_archive,
            },
        )
        result = SyncCoordinator(gateway, store).sync()
        assert result.changed

    pointer = json.loads((store.root / "active-generation.json").read_text())
    assert pointer["schema_version"] == 2
    assert pointer["components"]["index"]["generation"] == "index-5"
    assert pointer["history"]["index"] == ["index-5", "index-4", "index-3", "index-2"]
    assert sorted(path.name for path in (store.root / "generations" / "index").iterdir()) == [
        "index-2",
        "index-3",
        "index-4",
        "index-5",
    ]
    assert not (store.root / "current").exists()


def test_corrupt_download_does_not_change_active_pointer_or_backups(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    store = FilesystemGenerationStore(tmp_path / "cache")
    old_i = archive(source, GenerationComponentKind.INDEX, "i-old", "old")
    old_c = archive(source, GenerationComponentKind.CUSTOMERS, "c-old", "old")
    initial = manifest(
        component(GenerationComponentKind.INDEX, "i-old", old_i),
        component(GenerationComponentKind.CUSTOMERS, "c-old", old_c),
    )
    SyncCoordinator(
        FakeGateway(
            initial,
            {
                (GenerationComponentKind.INDEX, "i-old"): old_i,
                (GenerationComponentKind.CUSTOMERS, "c-old"): old_c,
            },
        ),
        store,
    ).sync()
    pointer_before = (store.root / "active-generation.json").read_bytes()
    dirs_before = sorted(str(path.relative_to(store.root)) for path in store.root.rglob("*") if path.is_dir())

    broken = source / "broken.zip"
    broken.write_bytes(b"not the declared archive")
    next_i = component(GenerationComponentKind.INDEX, "i-next", old_i)
    object.__setattr__(next_i, "generation", "i-next")
    failing = FakeGateway(
        manifest(next_i, initial.customers),
        {(GenerationComponentKind.INDEX, "i-next"): broken},
    )
    with pytest.raises(SyncError):
        SyncCoordinator(failing, store).sync()

    assert (store.root / "active-generation.json").read_bytes() == pointer_before
    assert sorted(str(path.relative_to(store.root)) for path in store.root.rglob("*") if path.is_dir()) == dirs_before


def test_legacy_symlink_is_imported_without_modifying_generation(tmp_path):
    root = tmp_path / "cache"
    generation = root / "generations" / "legacy-1"
    (generation / "index" / "catalog").mkdir(parents=True)
    (generation / "index" / "catalog" / "active.db").write_text("index")
    (generation / "customers.db").write_text("customers")
    try:
        (root / "current").symlink_to(Path("generations") / "legacy-1", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    before = {path.relative_to(generation): path.read_bytes() for path in generation.rglob("*") if path.is_file()}

    store = FilesystemGenerationStore(root)
    assert store.import_legacy_symlink()
    assert store.current_generation(GenerationComponentKind.INDEX) == "legacy-1"
    assert store.current_generation(GenerationComponentKind.CUSTOMERS) == "legacy-1"
    after = {path.relative_to(generation): path.read_bytes() for path in generation.rglob("*") if path.is_file()}
    assert after == before


def test_v1_combined_generation_is_downloaded_once_and_activates_both_components(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    content_index = b"legacy index"
    content_customers = b"legacy customers"
    generation = "legacy-combined-1"
    legacy_archive = source / f"{generation}.zip"
    inner_manifest = {
        "schema_version": 1,
        "generation": generation,
        "created_at": NOW,
        "files": [
            {
                "path": "index/catalog/active.db",
                "size": len(content_index),
                "sha256": hashlib.sha256(content_index).hexdigest(),
            },
            {
                "path": "customers.db",
                "size": len(content_customers),
                "sha256": hashlib.sha256(content_customers).hexdigest(),
            },
        ],
    }
    with zipfile.ZipFile(legacy_archive, "w") as bundle:
        bundle.writestr("index/catalog/active.db", content_index)
        bundle.writestr("customers.db", content_customers)
        bundle.writestr("manifest.json", json.dumps(inner_manifest))
    legacy_manifest = GenerationManifest.from_dict(
        {
            "schema_version": 1,
            "generation": generation,
            "created_at": NOW,
            "archive": legacy_archive.name,
            "size": legacy_archive.stat().st_size,
            "sha256": sha256(legacy_archive),
        }
    )

    class CountingGateway(FakeGateway):
        calls = 0

        def download_component(self, _kind, _component_manifest, destination):
            self.calls += 1
            shutil.copy2(legacy_archive, destination)

    gateway = CountingGateway(legacy_manifest, {})
    store = FilesystemGenerationStore(tmp_path / "cache")
    result = SyncCoordinator(gateway, store).sync()

    assert gateway.calls == 1
    assert result.changed_components == ("index", "customers")
    assert (store.active_component_path(GenerationComponentKind.INDEX) / "index/catalog/active.db").read_bytes() == content_index
    assert (store.active_component_path(GenerationComponentKind.CUSTOMERS) / "customers.db").read_bytes() == content_customers
