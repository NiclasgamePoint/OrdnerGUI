"""Concurrency and incremental regressions using only generated temporary inputs."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from papagui_server.adapters import catalog, catalog_documents
from papagui_server.adapters.catalog import DocumentTextExtractor, SqliteCatalogIndexer
from papagui_server.domain.document_extraction import ExtractionBlock, ExtractionResult
from papagui_server.domain.models import ServerSettings


class SyntheticExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.version = "synthetic-v1"
        self.lock = threading.Lock()

    def fingerprint(self, _settings):
        return self.version

    def extract_document(self, path, _settings, _cancelled):
        with self.lock:
            self.calls.append(path.name)
        text = path.read_text(encoding="utf-8")
        return ExtractionResult(text=text, blocks=(ExtractionBlock(text),), status="ok")


def _source(tmp_path: Path, count: int = 1) -> Path:
    source = tmp_path / "synthetic-source"
    source.mkdir()
    for number in range(count):
        (source / f"document-{number:02}.txt").write_text(
            f"generated content {number}", encoding="utf-8"
        )
    return source


def _build(indexer, source, **kwargs):
    return indexer.build(
        source,
        source_id="synthetic",
        full_rebuild=kwargs.pop("full_rebuild", False),
        settings=kwargs.pop("settings", ServerSettings()),
        cancelled=kwargs.pop("cancelled", lambda: False),
        progress=kwargs.pop("progress", lambda *_: None),
        **kwargs,
    )


def _workers(monkeypatch, count: int) -> None:
    monkeypatch.setattr(
        catalog,
        "document_worker_budget",
        lambda *_args, **_kwargs: SimpleNamespace(workers=count),
    )


def _source_opens(monkeypatch, source: Path) -> list[str]:
    opened: list[str] = []
    original = Path.open

    def track(path, mode="r", *args, **kwargs):
        if source in path.parents and "r" in mode:
            opened.append(path.name)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track)
    return opened


def _file_rows(path):
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return {row["filename"]: dict(row) for row in connection.execute("SELECT * FROM files")}


def test_failed_forced_artifact_store_recovers_valid_cached_reference(tmp_path, monkeypatch):
    _workers(monkeypatch, 1)
    source = _source(tmp_path)

    class TimedExtractor(SyntheticExtractor):
        def extract_document(self, path, settings, cancelled):
            result = super().extract_document(path, settings, cancelled)
            return replace(result, duration_ms=len(self.calls))

    extractor = TimedExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)
    _build(indexer, source)
    original = _file_rows(indexer.active_path)["document-00.txt"]
    with monkeypatch.context() as patch:
        patch.setattr(indexer.extraction_store, "put", lambda *_args, **_kwargs: False)
        _build(indexer, source, force_extraction=True)
    failed = _file_rows(indexer.active_path)["document-00.txt"]
    assert failed["extraction_reason"] == "artifact_store_budget"
    assert (
        indexer.extraction_store.read(failed["content_hash"], failed["extraction_version"]) is None
    )

    _build(indexer, source)
    recovered = _file_rows(indexer.active_path)["document-00.txt"]
    assert recovered["extraction_status"] == "ok"
    assert recovered["extraction_version"] == original["extraction_version"]
    assert len(extractor.calls) == 2
    assert (
        indexer.extraction_store.read(recovered["content_hash"], recovered["extraction_version"])
        is not None
    )


@pytest.mark.parametrize("worker_count", [2, 3])
def test_workers_overlap_with_bounded_submissions_and_caller_thread_writes(
    tmp_path, monkeypatch, worker_count
):
    _workers(monkeypatch, worker_count)
    source = _source(tmp_path, 15)
    release = threading.Event()
    all_started = threading.Event()
    observations = []
    writer_threads = []
    caller = threading.get_ident()
    maximum_pending = 0
    pending = 0
    pending_lock = threading.Lock()

    class TrackedExecutor(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            nonlocal maximum_pending, pending
            with pending_lock:
                pending += 1
                maximum_pending = max(maximum_pending, pending)
            future = super().submit(*args, **kwargs)

            def finished(_future):
                nonlocal pending
                with pending_lock:
                    pending -= 1

            future.add_done_callback(finished)
            return future

    class GatedExtractor(SyntheticExtractor):
        active = 0
        maximum_active = 0

        def extract_document(self, path, settings, cancelled):
            with self.lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                if self.active == worker_count:
                    all_started.set()
            try:
                assert release.wait(5), "worker gate was not released"
                return super().extract_document(path, settings, cancelled)
            finally:
                with self.lock:
                    self.active -= 1

    real_connect = sqlite3.connect
    writes = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_VTABLE,
    }

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)

        def authorize(action, *_args):
            if action in writes:
                writer_threads.append(threading.get_ident())
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(catalog_documents, "ThreadPoolExecutor", TrackedExecutor)
    monkeypatch.setattr(sqlite3, "connect", connect)
    extractor = GatedExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)

    def observe():
        try:
            if all_started.wait(3):
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    status = indexer.status()
                    observations.append(status)
                    if status["queued_documents"] > 0:
                        break
                    time.sleep(0.001)
        finally:
            release.set()

    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    try:
        _build(indexer, source, progress=lambda *_: observations.append(indexer.status()))
    finally:
        release.set()
        observer.join(5)
    assert not observer.is_alive()
    assert extractor.maximum_active == worker_count
    assert worker_count < maximum_pending <= 2 * worker_count
    assert writer_threads and set(writer_threads) == {caller}
    assert any(item["active_workers"] == worker_count for item in observations)
    assert any(item["queued_documents"] > 0 for item in observations)
    assert all(0 <= item["active_workers"] <= worker_count for item in observations)
    assert all(0 <= item["queued_documents"] <= 2 * worker_count for item in observations)
    status = indexer.status()
    assert status["worker_limit"] == worker_count
    assert status["active_workers"] == status["queued_documents"] == 0
    assert status["discovered_documents"] == status["processed_documents"] == 15
    assert status["extracted_documents"] == status["read_documents"] == 15
    assert status["failed_documents"] == status["reused_documents"] == 0
    assert status["elapsed_seconds"] >= 0


def test_unchanged_warm_build_opens_no_source_and_changed_file_is_read(tmp_path, monkeypatch):
    _workers(monkeypatch, 2)
    source = _source(tmp_path, 5)
    extractor = SyntheticExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)
    _build(indexer, source)
    extractor.calls.clear()
    opened = _source_opens(monkeypatch, source)

    _build(indexer, source)
    assert opened == extractor.calls == []
    status = indexer.status()
    assert status["read_documents"] == status["extracted_documents"] == 0
    assert status["reused_documents"] == status["processed_documents"] == 5

    changed = source / "document-02.txt"
    changed.write_text("generated changed content, different size", encoding="utf-8")
    _build(indexer, source)
    assert set(opened) == {changed.name}
    assert extractor.calls == [changed.name]
    assert indexer.status()["read_documents"] == 1
    assert indexer.status()["reused_documents"] == 4


@pytest.mark.parametrize("verification", ["verify_content", "full_rebuild"])
def test_explicit_verification_detects_same_stat_edit(tmp_path, monkeypatch, verification):
    _workers(monkeypatch, 2)
    source = _source(tmp_path)
    document = source / "document-00.txt"
    document.write_text("synthetic alpha", encoding="utf-8")
    extractor = SyntheticExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)
    _build(indexer, source)
    previous_hash = _file_rows(indexer.active_path)[document.name]["content_hash"]
    before = document.stat()
    document.write_text("synthetic bravo", encoding="utf-8")
    os.utime(document, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert document.stat().st_size == before.st_size
    opened = _source_opens(monkeypatch, source)
    extractor.calls.clear()

    _build(indexer, source)
    assert opened == extractor.calls == []
    assert _file_rows(indexer.active_path)[document.name]["content_hash"] == previous_hash
    _build(indexer, source, **{verification: True})
    assert set(opened) == {document.name}
    assert extractor.calls == [document.name]
    assert _file_rows(indexer.active_path)[document.name]["content_hash"] != previous_hash
    assert indexer.status()["verify_content"] is True


def test_format_version_invalidates_only_affected_unchanged_documents(tmp_path, monkeypatch):
    _workers(monkeypatch, 2)
    source = _source(tmp_path)
    (source / "synthetic.docx").write_text("generated office fixture", encoding="utf-8")

    class VersionedExtractor(DocumentTextExtractor):
        def __init__(self):
            super().__init__()
            self.versions = {"txt": "text-1", "docx": "office-1"}
            self.calls = []

        def fingerprint(self, _settings, extension=None):
            return self.versions.get(extension, "synthetic-combined")

        def extract_document(self, path, _settings, _cancelled):
            self.calls.append(path.name)
            return ExtractionResult(text=path.read_text(encoding="utf-8"), status="ok")

    extractor = VersionedExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)
    _build(indexer, source)
    extractor.calls.clear()
    opened = _source_opens(monkeypatch, source)
    extractor.versions["txt"] = "text-2"
    _build(indexer, source)
    assert extractor.calls == ["document-00.txt"]
    assert set(opened) <= {"document-00.txt"}
    assert indexer.status()["reused_documents"] == 1


@pytest.mark.parametrize(
    "disabled_settings,reason",
    [
        (ServerSettings(content_indexing_enabled=False), "content_indexing_disabled"),
        (ServerSettings(content_extensions="md"), "extension_disabled"),
    ],
)
def test_eligibility_changes_replace_content_without_reading_disabled_files(
    tmp_path, monkeypatch, disabled_settings, reason
):
    _workers(monkeypatch, 2)
    source = _source(tmp_path)
    extractor = SyntheticExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)
    _build(indexer, source)
    extractor.calls.clear()
    opened = _source_opens(monkeypatch, source)
    _build(indexer, source, settings=disabled_settings)
    row = _file_rows(indexer.active_path)["document-00.txt"]
    assert (row["content_eligible"], row["full_text_indexed"]) == (0, 0)
    assert (row["extraction_status"], row["extraction_reason"]) == ("unsupported", reason)
    assert opened == extractor.calls == []
    _build(indexer, source)
    row = _file_rows(indexer.active_path)["document-00.txt"]
    assert (row["content_eligible"], row["full_text_indexed"], row["extraction_status"]) == (
        1,
        1,
        "ok",
    )


def test_custom_extractor_exception_is_per_document_and_sanitized(tmp_path, monkeypatch):
    _workers(monkeypatch, 2)
    source = _source(tmp_path, 5)

    class FailingExtractor(SyntheticExtractor):
        def extract_document(self, path, settings, cancelled):
            if path.name == "document-02.txt":
                raise RuntimeError("synthetic private content that must not be persisted")
            return super().extract_document(path, settings, cancelled)

    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", FailingExtractor())
    _build(indexer, source)
    rows = _file_rows(indexer.active_path)
    assert Counter(row["extraction_status"] for row in rows.values()) == {"ok": 4, "error": 1}
    assert rows["document-02.txt"]["extraction_reason"]
    assert "private" not in str(rows)
    assert indexer.status()["failed_documents"] == 1
    assert indexer.status()["processed_documents"] == 5


def test_failure_is_reused_until_retry_due_and_success_stays_warm(tmp_path, monkeypatch):
    _workers(monkeypatch, 2)
    source = _source(tmp_path, 2)
    now = [1_800_000_000.0]
    monkeypatch.setattr("papagui_server.adapters.extraction_store.time.time", lambda: now[0])

    class RetryExtractor(SyntheticExtractor):
        def extract_document(self, path, settings, cancelled):
            result = super().extract_document(path, settings, cancelled)
            if path.name == "document-00.txt" and self.calls.count(path.name) == 1:
                return ExtractionResult(status="timeout", reason="synthetic_timeout")
            return result

    extractor = RetryExtractor()
    settings = ServerSettings(extraction_retry_delay_seconds=300)
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)
    _build(indexer, source, settings=settings)
    before = list(extractor.calls)
    opened = _source_opens(monkeypatch, source)
    now[0] += 299
    _build(indexer, source, settings=settings)
    assert extractor.calls == before and opened == []
    now[0] += 2
    _build(indexer, source, settings=settings)
    assert extractor.calls[len(before) :] == ["document-00.txt"]
    assert set(opened) <= {"document-00.txt"}
    assert _file_rows(indexer.active_path)["document-00.txt"]["extraction_status"] == "ok"
    extractor.calls.clear()
    opened.clear()
    _build(indexer, source, settings=settings)
    assert extractor.calls == opened == []


def test_exhausted_failure_retries_stay_warm_after_deadline(tmp_path, monkeypatch):
    _workers(monkeypatch, 2)
    source = _source(tmp_path)
    now = [1_800_000_000.0]
    monkeypatch.setattr("papagui_server.adapters.extraction_store.time.time", lambda: now[0])

    class AlwaysFailingExtractor(SyntheticExtractor):
        def extract_document(self, path, settings, cancelled):
            super().extract_document(path, settings, cancelled)
            return ExtractionResult(status="timeout", reason="synthetic_timeout")

    extractor = AlwaysFailingExtractor()
    settings = ServerSettings(extraction_retry_attempts=2, extraction_retry_delay_seconds=300)
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", extractor)
    _build(indexer, source, settings=settings)
    now[0] += 301
    _build(indexer, source, settings=settings)
    assert extractor.calls == ["document-00.txt", "document-00.txt"]
    extractor.calls.clear()
    opened = _source_opens(monkeypatch, source)
    now[0] += 301
    _build(indexer, source, settings=settings)
    assert extractor.calls == opened == []
    assert _file_rows(indexer.active_path)["document-00.txt"]["extraction_status"] == "timeout"


def test_cancel_joins_workers_keeps_active_and_resumes_without_completed_source_reads(
    tmp_path, monkeypatch
):
    _workers(monkeypatch, 2)
    source = _source(tmp_path)
    indexer = SqliteCatalogIndexer(tmp_path / "synthetic-state", SyntheticExtractor())
    _build(indexer, source)
    before = indexer.active_path.read_bytes()
    backups_before = set(indexer.backup_root.glob("*.db"))
    for number in range(12):
        (source / f"new-{number:02}.txt").write_text(
            f"generated new content {number}", encoding="utf-8"
        )
    stop = threading.Event()
    paired = threading.Event()

    class CancelExtractor(SyntheticExtractor):
        def __init__(self):
            super().__init__()
            self.threads = set()
            self.started = 0

        def extract_document(self, path, settings, cancelled):
            with self.lock:
                self.threads.add(threading.current_thread())
                self.started += 1
                first = self.started == 1
                if self.started >= 2:
                    paired.set()
            assert paired.wait(5), "cancellation fixture needs concurrent workers"
            if first:
                return super().extract_document(path, settings, cancelled)
            assert stop.wait(5), "build did not cancel after completed extraction"
            return ExtractionResult(status="partial", reason="cancelled")

    extractor = CancelExtractor()
    indexer.extractor = extractor
    completed = []

    def progress(_count, relative):
        if relative.startswith("new-"):
            completed.append(relative)
            stop.set()

    try:
        with pytest.raises(InterruptedError):
            _build(indexer, source, cancelled=stop.is_set, progress=progress)
    finally:
        stop.set()
    assert completed
    assert indexer.active_path.read_bytes() == before
    assert set(indexer.backup_root.glob("*.db")) == backups_before
    assert indexer.resume_state_path.is_file()
    assert extractor.threads and all(not thread.is_alive() for thread in extractor.threads)
    assert indexer.status()["active_workers"] == indexer.status()["queued_documents"] == 0
    completed_rows = {
        name
        for name, row in _file_rows(indexer.resume_path).items()
        if row["extraction_status"] == "ok"
    }
    assert set(completed) <= completed_rows
    opened = _source_opens(monkeypatch, source)
    resumed = SyntheticExtractor()
    indexer.extractor = resumed
    _build(indexer, source)
    assert not (completed_rows & set(opened))
    assert not (completed_rows & set(resumed.calls))
    assert len(_file_rows(indexer.active_path)) == 13
    assert not indexer.resume_state_path.exists()
