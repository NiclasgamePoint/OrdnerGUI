from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from papagui_server.adapters.generations import (
    GenerationV2Publisher,
    atomic_json,
    sha256_file,
    snapshot_sqlite,
)
from papagui_server.domain.errors import ResourceNotFoundError


def _database(path: Path, value: str = "value") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS values_table(value TEXT)")
    connection.execute("DELETE FROM values_table")
    connection.execute("INSERT INTO values_table VALUES (?)", (value,))
    connection.commit()
    connection.close()


def test_atomic_json_snapshot_and_hash_helpers(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "nested" / "value.json"
    atomic_json(target, {"ümlaut": "✓"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ümlaut": "✓"}
    assert len(sha256_file(target)) == 64

    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot" / "copy.db"
    _database(source, "copied")
    snapshot_sqlite(source, destination)
    connection = sqlite3.connect(destination)
    try:
        assert connection.execute("SELECT value FROM values_table").fetchone()[0] == "copied"
    finally:
        connection.close()

    monkeypatch.setattr(
        "papagui_server.adapters.generations.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError):
        atomic_json(tmp_path / "failed.json", {"x": 1})
    assert not list(tmp_path.glob(".failed.json.*.tmp"))


def test_publish_all_filters_internal_files_and_validates_archives(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _database(data / "index" / "catalog" / "active.db", "index")
    (data / "index" / "metadata.json").write_text("{}", encoding="utf-8")
    (data / "index" / "ignored.txt").write_text("ignored", encoding="utf-8")
    (data / "index" / "builds").mkdir()
    (data / "index" / "builds" / "resume.db").write_bytes(b"ignored")
    _database(data / "customers.db", "customers")
    publisher = GenerationV2Publisher(data)
    current = publisher.publish_all()
    assert set(current["components"]) == {"index", "customers"}
    for component, descriptor in current["components"].items():
        assert publisher.descriptor(component, descriptor["generation"]) == descriptor
        archive = publisher.archive_path(component, descriptor["generation"])
        with zipfile.ZipFile(archive) as bundle:
            assert "manifest.json" in bundle.namelist()
            if component == "index":
                assert "index/catalog/active.db" in bundle.namelist()
                assert "index/metadata.json" in bundle.namelist()
                assert "index/ignored.txt" not in bundle.namelist()
                assert "index/builds/resume.db" not in bundle.namelist()

    index = current["components"]["index"]
    archive = publisher.archive_path("index", index["generation"])
    archive.write_bytes(archive.read_bytes() + b"corrupt")
    with pytest.raises(ResourceNotFoundError, match="unvollständig"):
        publisher.archive_path("index", index["generation"])


def test_generation_lookup_rejects_invalid_data_and_missing_sources(tmp_path: Path) -> None:
    publisher = GenerationV2Publisher(tmp_path / "data")
    assert publisher.current() is None
    publisher.active_path.parent.mkdir(parents=True)
    for value in ("bad json", "[]", '{"schema_version": 1}'):
        publisher.active_path.write_text(value, encoding="utf-8")
        assert publisher.current() is None
    with pytest.raises(ResourceNotFoundError, match="kein Index"):
        publisher.publish_index()
    (publisher.data_path / "index").mkdir()
    (publisher.data_path / "index" / "ignored.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ResourceNotFoundError, match="veröffentlichbar"):
        publisher.publish_index()
    with pytest.raises(ResourceNotFoundError, match="Kundendatenbank"):
        publisher.publish_customers()
    with pytest.raises(ResourceNotFoundError, match="Unbekannte"):
        publisher.descriptor("unknown", "generation")
    with pytest.raises(ResourceNotFoundError, match="Ungültige"):
        publisher.archive_path("index", "../bad")
    with pytest.raises(ResourceNotFoundError, match="nicht gefunden"):
        publisher.descriptor("index", "20260101T000000.000000Z-deadbeef")


def test_checksum_mismatch_and_delete_index_pointer_behaviour(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _database(data / "index" / "active.db")
    _database(data / "customers.db")
    publisher = GenerationV2Publisher(data)
    index = publisher.publish_index()
    customers = publisher.publish_customers()
    archive = publisher.archive_path("index", index["generation"])
    original = archive.read_bytes()
    archive.write_bytes(b"X" * len(original))
    with pytest.raises(ResourceNotFoundError, match="beschädigt"):
        publisher.archive_path("index", index["generation"])
    publisher.delete_index_generations()
    current = publisher.current()
    assert current["components"]["index"] is None
    assert current["components"]["customers"] == customers
    assert not (publisher.root / "index").exists()
    publisher.delete_index_generations()

    only_index_data = tmp_path / "only-index"
    _database(only_index_data / "index" / "active.db")
    only = GenerationV2Publisher(only_index_data)
    only.publish_index()
    only.delete_index_generations()
    assert only.current() is None


def test_failed_snapshot_does_not_change_previous_generation(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    _database(data / "index" / "active.db", "good")
    publisher = GenerationV2Publisher(data)
    first = publisher.publish_index()
    before = publisher.current()
    archives = list((publisher.root / "index" / "archives").glob("*.zip"))

    monkeypatch.setattr(
        "papagui_server.adapters.generations.snapshot_sqlite",
        lambda *_args: (_ for _ in ()).throw(OSError("copy failed")),
    )
    with pytest.raises(OSError, match="copy failed"):
        publisher.publish_index()
    assert publisher.current() == before
    assert list((publisher.root / "index" / "archives").glob("*.zip")) == archives
    assert publisher.descriptor("index", first["generation"]) == first
