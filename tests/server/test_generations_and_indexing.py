from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
import zipfile

from papagui_contracts import GenerationManifest

from papagui_server.adapters.generations import GenerationV2Publisher, sha256_file
from papagui_server.composition import RuntimeConfiguration, build_container


def _runtime(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    return build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=tmp_path / "data",
            config_path=tmp_path / "config",
            client_token="client-token-123",
            bootstrap_admin_password="admin-password-123",
        )
    )


def test_index_run_uses_portable_paths_and_recognizes_customer(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    project = runtime.configuration.source_path / "Planung" / "2026" / "Mustermann, Köln"
    project.mkdir(parents=True)
    (project / "angebot.txt").write_text("Angebot für Max Mustermann", encoding="utf-8")

    assert runtime.coordinator.run_once(full_rebuild=True) == 0
    database = runtime.configuration.data_path / "index" / "catalog" / "active.db"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT path, source_id, relative_path FROM files"
        ).fetchone()
    finally:
        connection.close()
    assert row[0].startswith("source://primary/")
    assert row[1] == "primary"
    assert row[2] == "Planung/2026/Mustermann, Köln/angebot.txt"
    assert str(runtime.configuration.source_path) not in row[0]

    customers = runtime.customers.list_customers()
    assert len(customers) == 1
    assert customers[0]["display_name"] == "Mustermann"
    assert customers[0]["folder_path"].startswith("source://primary/")
    manifest = GenerationManifest.from_dict(runtime.publisher.current())
    assert manifest.index.generation
    assert manifest.customers.generation


def test_components_rotate_active_plus_three_predecessors(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    project = runtime.configuration.source_path / "Planung" / "2026" / "Beta, Bonn"
    project.mkdir(parents=True)
    source = project / "status.txt"
    for number in range(5):
        source.write_text(f"Stand {number}", encoding="utf-8")
        assert runtime.coordinator.run_once(full_rebuild=number == 0) == 0

    generation_root = runtime.configuration.data_path / "generations-v2"
    for component in ("index", "customers"):
        archives = sorted((generation_root / component / "archives").glob("*.zip"))
        manifests = sorted((generation_root / component / "manifests").glob("*.json"))
        assert len(archives) == 4
        assert len(manifests) == 4
        descriptor = json.loads(manifests[-1].read_text(encoding="utf-8"))
        assert descriptor["sha256"] == sha256_file(archives[-1])
        with zipfile.ZipFile(archives[-1]) as bundle:
            inner = json.loads(bundle.read("manifest.json"))
            assert inner["component"] == component


def test_failed_publication_does_not_rotate_valid_generations(tmp_path: Path) -> None:
    publisher = GenerationV2Publisher(tmp_path / "data")
    try:
        publisher.publish_index()
    except Exception:
        pass
    assert not list((tmp_path / "data" / "generations-v2").rglob("*.zip"))


def test_scheduler_can_start_without_an_immediate_index_run(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.coordinator.start(run_on_start=False)
    try:
        time.sleep(0.05)
        assert not (runtime.configuration.data_path / "index" / "catalog" / "active.db").exists()
        assert runtime.coordinator.status()["index"]["state"] == "idle"
    finally:
        runtime.coordinator.stop()
