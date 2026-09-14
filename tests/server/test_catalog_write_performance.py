"""Catalog write complexity and durability regressions on synthetic databases."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from papagui_server.adapters.catalog_storage import CatalogSqliteWriter
from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.adapters.extraction_store import ExtractionStore, artifact_identity
from papagui_server.domain.document_extraction import ExtractionResult
from papagui_server.domain.models import ServerSettings


def result(key="a", text="Synthetisch äöü"):
    return ExtractionResult(text=text, status="ok", content_hash=key, version_hash="v1")


def stored_bytes(store):
    with closing(sqlite3.connect(store.database_path)) as connection:
        actual = sum(
            connection.execute(
                f"SELECT COALESCE(SUM(length(CAST(result_json AS BLOB))),0) FROM {table}"
            ).fetchone()[0]
            for table in ("document_extractions", "immutable_document_extractions")
        )
        counted = connection.execute("SELECT payload_bytes FROM extraction_storage_usage").fetchone()[0]
    assert counted == actual
    return actual


def test_fts_removal_uses_direct_lookup_with_legacy_rowids():
    writer = CatalogSqliteWriter()
    with closing(sqlite3.connect(":memory:")) as connection:
        writer.initialize(connection)
        connection.executemany(
            "INSERT INTO file_content_fts(rowid,path,content) VALUES (?,?,?)",
            [(1000 + 2 * number, f"source://synthetic/{number}", "synthetic searchable") for number in range(2000)],
        )
        # Reopening a staged legacy catalog must not assume FTS rowids match files.id.
        writer.initialize(connection)
        steps = []
        connection.set_progress_handler(lambda: steps.append(True) or 0, 100)
        writer.remove_content(connection, "source://synthetic/1999")
        connection.set_progress_handler(None, 0)
        assert len(steps) < 10  # A full FTS/path scan would visit thousands of rows.
        assert connection.execute(
            "SELECT COUNT(*) FROM file_content_fts WHERE file_content_fts MATCH 'searchable'"
        ).fetchone()[0] == 1999
        assert connection.execute("SELECT path FROM file_content_fts WHERE rowid=1000").fetchone()[0] == "source://synthetic/0"
        writer.remove_content(connection, "source://synthetic/1999")  # Already absent is harmless.


def test_usage_counter_migrates_old_cache_and_tracks_all_writers(tmp_path):
    store = ExtractionStore(tmp_path / "artifacts.db")
    first = result()
    assert store.put(first)
    original = stored_bytes(store)
    with closing(sqlite3.connect(store.database_path)) as connection:
        for table in ("document_extractions", "immutable_document_extractions"):
            for action in ("insert", "update", "delete"):
                connection.execute(f"DROP TRIGGER {table}_usage_{action}")
        connection.execute("DROP TABLE extraction_storage_usage")
        connection.commit()
    # The first writer upgrades an existing cache without dropping its artifacts.
    assert store.put(first)
    assert stored_bytes(store) == original
    assert store.read(first.content_hash, artifact_identity(first)) == first
    changed = replace(first, text="Größerer synthetischer Inhalt")
    assert ExtractionStore(store.database_path).put(changed)
    assert stored_bytes(store) > original
    before = stored_bytes(store)
    assert not store.put(result("rejected"), max_store_mb=0)
    assert stored_bytes(store) == before
    assert store.put(result("b"))
    with closing(sqlite3.connect(store.database_path)) as connection:
        connection.execute("UPDATE document_extractions SET touched_at=0")
        connection.execute("UPDATE immutable_document_extractions SET touched_at=0")
        connection.commit()
    store.cleanup(protected=[(first.content_hash, artifact_identity(first))], retention_days=0)
    assert 0 < stored_bytes(store) < before
    assert store.read(first.content_hash, artifact_identity(first)) == first


def test_buffered_cache_checkpoints_commit_and_errors_rollback(tmp_path):
    store = ExtractionStore(tmp_path / "artifacts.db")
    first, second, third = result("a"), result("b"), result("c")
    with pytest.raises(ValueError, match="synthetic failure"):
        with store.buffered_writes():
            assert store.put(first)
            assert store.peek(first.content_hash, first.version_hash) is None
            store.flush()
            assert store.peek(first.content_hash, first.version_hash) == first
            assert store.put(second)
            raise ValueError("synthetic failure")
    assert store.read(first.content_hash, artifact_identity(first)) == first
    assert store.read(second.content_hash, artifact_identity(second)) is None
    stored_bytes(store)
    with pytest.raises(InterruptedError):
        with store.buffered_writes():
            assert store.put(third)
            raise InterruptedError("controlled cancellation")
    assert store.read(third.content_hash, artifact_identity(third)) == third
    stored_bytes(store)


def test_empty_write_batch_does_not_create_a_cache(tmp_path):
    store = ExtractionStore(tmp_path / "artifacts.db")
    with store.buffered_writes():
        store.flush()
    assert not store.database_path.exists()


def test_large_artifact_flushes_before_readers_can_block_on_the_writer(tmp_path):
    store = ExtractionStore(tmp_path / "artifacts.db")
    large = result(text="synthetic content " * 100_000)
    with store.buffered_writes():
        assert store.put(large)
        with ThreadPoolExecutor(max_workers=1) as executor:
            read = executor.submit(store.peek, large.content_hash, large.version_hash)
            assert read.result(timeout=2) == large
    stored_bytes(store)


def test_catalog_checkpoint_only_references_durable_artifacts(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for number in range(30):
        (source / f"document-{number:02}.txt").write_text(f"Synthetic document {number}")

    class Reader:
        def fingerprint(self, _settings):
            return "synthetic-v1"

        def extract_document(self, path, _settings, _cancelled):
            return ExtractionResult(text=path.read_text(), status="ok")

    indexer = SqliteCatalogIndexer(tmp_path / "state", Reader())
    checkpoints = []

    def progress(count, _relative):
        if count != 25:
            return
        with closing(sqlite3.connect(indexer.resume_path)) as connection:
            rows = connection.execute("SELECT content_hash,extraction_version FROM files").fetchall()
        assert len(rows) == 25
        assert all(indexer.extraction_store.read(*row) is not None for row in rows)
        checkpoints.append(count)

    indexer.build(
        source, source_id="synthetic", full_rebuild=False, settings=ServerSettings(),
        cancelled=lambda: False, progress=progress,
    )
    assert checkpoints == [25]
