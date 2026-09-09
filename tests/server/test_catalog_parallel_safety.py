"""Synthetic regressions for cache sharing, staged publication and forced resume."""

from __future__ import annotations

from contextlib import closing
import hashlib
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from papagui_server.adapters import catalog, catalog_documents
from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.domain.document_extraction import ExtractionResult
from papagui_server.domain.models import ServerSettings


class Reader:
    def __init__(self):
        self.calls = []

    def fingerprint(self, settings):
        return "synthetic-safety-v1"

    def extract_document(self, path, settings, cancelled):
        self.calls.append(path.name)
        return ExtractionResult(text=path.read_text(), status="ok")


def build(indexer, source, **kwargs):
    return indexer.build(
        source,
        source_id="synthetic",
        full_rebuild=False,
        settings=ServerSettings(),
        cancelled=kwargs.pop("cancelled", lambda: False),
        progress=kwargs.pop("progress", lambda *_: None),
        **kwargs,
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "document_worker_budget", lambda _: SimpleNamespace(workers=1))
    source = tmp_path / "synthetic-source"
    source.mkdir()
    return source, SqliteCatalogIndexer(tmp_path / "synthetic-state", Reader())


def test_changed_shared_leader_cannot_poison_unchanged_copy(setup):
    source, indexer = setup
    for name in ("a.txt", "b.txt"):
        (source / name).write_text("original synthetic text")

    class ChangingReader(Reader):
        def extract_document(self, path, settings, cancelled):
            if not self.calls:
                path.write_text("changed synthetic text with different size")
            return super().extract_document(path, settings, cancelled)

    indexer.extractor = ChangingReader()
    build(indexer, source)
    with closing(sqlite3.connect(indexer.active_path)) as connection:
        rows = connection.execute(
            "SELECT f.relative_path,x.content FROM files f JOIN file_content_fts x ON x.path=f.path"
        ).fetchall()
    for relative, indexed in rows:
        assert indexed == (source / relative).read_text()
    if indexer.extraction_store.database_path.exists():
        original_hash = hashlib.sha256(b"original synthetic text").hexdigest()
        with closing(sqlite3.connect(indexer.extraction_store.database_path)) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM document_extractions "
                    "WHERE content_hash=? AND result_json LIKE '%changed synthetic%'",
                    (original_hash,),
                ).fetchone()[0]
                == 0
            )


def test_all_identical_copies_report_artifact_store_failure(setup, monkeypatch):
    source, indexer = setup
    for name in ("a.txt", "b.txt"):
        (source / name).write_text("identical synthetic text")
    monkeypatch.setattr(indexer.extraction_store, "put", lambda *_args, **_kwargs: False)
    build(indexer, source)
    with closing(sqlite3.connect(indexer.active_path)) as connection:
        statuses = connection.execute(
            "SELECT extraction_status,extraction_reason FROM files"
        ).fetchall()
    assert statuses == [("partial", "artifact_store_budget")] * 2


def test_cancellation_after_readers_finish_preserves_active_catalog(setup, monkeypatch):
    source, indexer = setup
    (source / "a.txt").write_text("original synthetic text")
    build(indexer, source)
    active_before = indexer.active_path.read_bytes()
    (source / "a.txt").write_text("changed synthetic text")
    cancelled = threading.Event()
    run = catalog_documents.ParallelDocuments.run

    def cancel_after_readers(pipeline, **kwargs):
        result = run(pipeline, **kwargs)
        cancelled.set()
        return result

    monkeypatch.setattr(catalog_documents.ParallelDocuments, "run", cancel_after_readers)
    with pytest.raises(InterruptedError):
        build(indexer, source, cancelled=cancelled.is_set)
    assert indexer.active_path.read_bytes() == active_before


def test_forced_resume_reuses_completed_documents_in_same_run(setup):
    source, indexer = setup
    for number in range(4):
        (source / f"{number}.txt").write_text(f"synthetic content {number}")
    build(indexer, source)
    cancelled = threading.Event()
    completed = []

    def cancel_after_first(_count, relative):
        completed.append(relative)
        cancelled.set()

    with pytest.raises(InterruptedError):
        build(
            indexer,
            source,
            force_extraction=True,
            cancelled=cancelled.is_set,
            progress=cancel_after_first,
        )
    assert completed
    indexer.extractor.calls.clear()
    build(indexer, source, force_extraction=True)
    assert not set(completed) & set(indexer.extractor.calls)


def test_scoped_force_preserves_normal_discovery_without_reextracting_unchanged_other_projects(
    setup,
):
    source, indexer = setup
    selected = source / "Service" / "2026" / "Selected GmbH" / "selected.txt"
    changed = source / "Service" / "2026" / "Changed GmbH" / "changed.txt"
    unchanged = source / "Service" / "2026" / "Unchanged GmbH" / "unchanged.txt"
    for path in (selected, changed, unchanged):
        path.parent.mkdir(parents=True)
        path.write_text(f"original synthetic text for {path.name}")
    build(indexer, source)
    with closing(sqlite3.connect(indexer.active_path)) as connection:
        selected_id = connection.execute(
            "SELECT id FROM project_roots WHERE relative_path=?",
            (selected.parent.relative_to(source).as_posix(),),
        ).fetchone()[0]
    changed.write_text("changed synthetic text in a different project")
    indexer.extractor.calls.clear()
    build(indexer, source, force_extraction=True, project_root_ids=[selected_id])
    assert set(indexer.extractor.calls) == {selected.name, changed.name}
