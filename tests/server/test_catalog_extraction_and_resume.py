from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import zipfile

import pytest

from papagui_server.adapters.catalog import (
    DocumentTextExtractor,
    ExternalCommandRunner,
    ExtractionResourcePolicy,
    SqliteCatalogIndexer,
)
from papagui_server.adapters.generations import sha256_file
from papagui_server.domain.models import ServerSettings


class RecordingRunner:
    def __init__(self, pdftotext: bytes = b"") -> None:
        self.pdftotext = pdftotext
        self.calls: list[tuple[list[str], int]] = []

    def run(self, command: list[str], *, timeout: int) -> bytes:
        self.calls.append((command, timeout))
        if command[0] == "pdftotext":
            return self.pdftotext
        if command[0] == "pdftoppm":
            prefix = Path(command[-1])
            prefix.with_name(prefix.name + "-1.png").write_bytes(b"image")
            prefix.with_name(prefix.name + "-2.png").write_bytes(b"image")
            return b""
        if command[0] == "tesseract":
            return f"OCR {Path(command[1]).stem}".encode()
        return b"external text"


def test_resource_policy_profiles_and_preferred_document() -> None:
    gentle = ExtractionResourcePolicy.for_document(
        Path("scan.pdf"), ServerSettings(resource_profile="gentle")
    )
    assert (gentle.render_dpi, gentle.pause_seconds, gentle.ocr_pages) == (150, 0.05, 5)
    fast = ExtractionResourcePolicy.for_document(
        Path("Angebot Kunde.pdf"), ServerSettings(resource_profile="fast")
    )
    assert (fast.render_dpi, fast.pause_seconds, fast.ocr_pages) == (250, 0.0, 25)


def test_external_runner_handles_success_failure_timeout_and_missing(monkeypatch) -> None:
    runner = ExternalCommandRunner()

    class Result:
        returncode = 0
        stdout = b"okay"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    assert runner.run(["tool"], timeout=1) == b"okay"
    Result.returncode = 2
    assert runner.run(["tool"], timeout=1) == b""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("tool", 1)),
    )
    assert runner.run(["tool"], timeout=1) == b""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    assert runner.run(["tool"], timeout=1) == b""


def test_text_office_and_legacy_document_extractors(tmp_path: Path) -> None:
    runner = RecordingRunner()
    extractor = DocumentTextExtractor(runner)
    settings = ServerSettings(max_extracted_characters=8)
    text = tmp_path / "note.txt"
    text.write_text("123456789", encoding="utf-8")
    assert extractor.extract(text, settings) == "12345678"
    directory = tmp_path / "broken.txt"
    directory.mkdir()
    assert extractor.extract(directory, settings) == ""
    assert extractor.extract(tmp_path / "file.doc", settings) == "external"
    assert extractor.extract_document(tmp_path / "file.xls", settings).status == "error"
    assert extractor.extract(tmp_path / "file.bin", settings) == ""

    docx = tmp_path / "document.docx"
    with zipfile.ZipFile(docx, "w") as bundle:
        bundle.writestr("word/document.xml", "<root><p>Alpha</p><p>Beta</p></root>")
    assert extractor.extract(docx, ServerSettings()) == "Alpha\nBeta"
    import openpyxl

    xlsx = tmp_path / "sheet.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Gamma", "Delta"])
    workbook.save(xlsx)
    workbook.close()
    assert extractor.extract(xlsx, ServerSettings()) == "Gamma\nDelta"
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not zip")
    assert extractor.extract(bad, settings) == ""
    missing_member = tmp_path / "missing.docx"
    with zipfile.ZipFile(missing_member, "w") as bundle:
        bundle.writestr("other.xml", "<bad")
    assert extractor.extract(missing_member, settings) == ""


def test_pdf_uses_bounded_ocr_and_honours_cancel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "papagui_server.adapters.catalog_extraction.time.sleep", lambda _seconds: None
    )
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"pdf")
    runner = RecordingRunner(b"short")
    extractor = DocumentTextExtractor(runner)
    settings = ServerSettings(
        resource_profile="gentle",
        ocr_extension_threshold=100,
        max_extracted_characters=100,
        ocr_timeout_seconds=7,
        pdf_text_timeout_seconds=9,
    )
    assert extractor.extract(pdf, settings) == "short\nOCR page-1\nOCR page-2"
    assert [call[0][0] for call in runner.calls] == [
        "pdftotext",
        "pdftoppm",
        "tesseract",
        "tesseract",
    ]
    assert runner.calls[0][1] == 9
    assert runner.calls[-1][1] == 7

    no_ocr_runner = RecordingRunner(b"already enough text")
    no_ocr = DocumentTextExtractor(no_ocr_runner)
    assert no_ocr.extract(pdf, ServerSettings(ocr_extension_threshold=5)) == "already enough text"
    assert len(no_ocr_runner.calls) == 1
    disabled = RecordingRunner()
    assert DocumentTextExtractor(disabled).extract(pdf, ServerSettings(ocr_enabled=False)) == ""
    assert len(disabled.calls) == 1
    cancelled = RecordingRunner()
    assert DocumentTextExtractor(cancelled).extract(pdf, settings, lambda: True) == ""


class CountingExtractor:
    def __init__(self, fail_name: str = "") -> None:
        self.paths: list[str] = []
        self.fail_name = fail_name

    def extract(self, path: Path, _settings, _cancelled) -> str:
        self.paths.append(path.name)
        if path.name == self.fail_name:
            raise RuntimeError("extraction failed")
        return path.read_text(encoding="utf-8")


def _build(indexer: SqliteCatalogIndexer, source: Path, *, full: bool, cancelled=lambda: False):
    progress: list[tuple[int, str]] = []
    path = indexer.build(
        source,
        source_id="primary",
        full_rebuild=full,
        settings=ServerSettings(),
        cancelled=cancelled,
        progress=lambda count, name: progress.append((count, name)),
    )
    return path, progress


def test_scanner_incremental_removal_exclusions_and_three_backups(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    keep = source / "service" / "2026" / "Customer" / "keep.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("one", encoding="utf-8")
    excluded = source / ".git"
    excluded.mkdir()
    (excluded / "ignored.txt").write_text("ignore", encoding="utf-8")
    (source / "large.txt").write_bytes(b"x" * 20)
    try:
        (source / "link.txt").symlink_to(keep)
    except OSError:
        pass
    extractor = CountingExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "data", extractor)
    settings = ServerSettings(max_file_size_mb=1)
    active = indexer.build(
        source,
        source_id="primary",
        full_rebuild=True,
        settings=settings,
        cancelled=lambda: False,
        progress=lambda *_args: None,
    )
    assert active.is_file()
    connection = sqlite3.connect(active)
    try:
        names = {row[0] for row in connection.execute("SELECT filename FROM files")}
    finally:
        connection.close()
    assert names == {"keep.txt", "large.txt"}

    for number in range(4):
        keep.write_text(f"value-{number}", encoding="utf-8")
        _build(indexer, source, full=False)
    assert len(list(indexer.backup_root.glob("*.db"))) == 3
    keep.unlink()
    _build(indexer, source, full=False)
    connection = sqlite3.connect(indexer.active_path)
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM files WHERE filename='keep.txt'").fetchone()[0]
            == 0
        )
    finally:
        connection.close()
    indexer.delete()
    assert not indexer.catalog_root.exists()
    indexer.delete()


def test_cancelled_scan_reuses_committed_documents_on_resume(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for number in range(30):
        (source / f"{number:02}.txt").write_text(str(number), encoding="utf-8")
    extractor = CountingExtractor()
    indexer = SqliteCatalogIndexer(tmp_path / "data", extractor)
    stop = threading.Event()

    def progress(count: int, _name: str) -> None:
        if count >= 3:
            stop.set()

    with pytest.raises(InterruptedError):
        indexer.build(
            source,
            source_id="primary",
            full_rebuild=True,
            settings=ServerSettings(),
            cancelled=stop.is_set,
            progress=progress,
        )
    assert indexer.resume_path.is_file()
    assert indexer.resume_state_path.is_file()
    connection = sqlite3.connect(indexer.resume_path)
    try:
        committed = {
            row[0]
            for row in connection.execute("SELECT filename FROM files WHERE extraction_status='ok'")
        }
    finally:
        connection.close()
    assert 0 < len(committed) < 30
    # Workers may finish parsing after cancellation without committing their results.
    # Only durable completed documents are guaranteed to avoid extraction on resume.
    extractor.paths.clear()
    _build(indexer, source, full=False)
    assert not committed.intersection(extractor.paths)
    assert len(extractor.paths) <= 30 - len(committed)
    connection = sqlite3.connect(indexer.active_path)
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM files WHERE extraction_status='ok'"
            ).fetchone()[0]
            == 30
        )
        assert connection.execute("SELECT COUNT(*) FROM file_content_fts").fetchone()[0] == 30
    finally:
        connection.close()
    assert not indexer.resume_state_path.exists()


def test_custom_parser_failure_is_isolated_and_successful_files_are_published(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "success.txt").write_text("generated success", encoding="utf-8")
    (source / "failure.txt").write_text("generated failure", encoding="utf-8")
    indexer = SqliteCatalogIndexer(tmp_path / "data", CountingExtractor("failure.txt"))
    _build(indexer, source, full=True)
    connection = sqlite3.connect(indexer.active_path)
    try:
        rows = {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT filename, extraction_status, extraction_reason FROM files"
            )
        }
        assert rows["success.txt"][0] == "ok"
        assert rows["failure.txt"] == ("error", "document_read_failed")
        assert "extraction failed" not in str(rows)
        assert connection.execute("SELECT COUNT(*) FROM file_content_fts").fetchone()[0] == 1
    finally:
        connection.close()
    assert not indexer.resume_state_path.exists()


def test_writer_failure_never_rotates_active_or_backups(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "success.txt").write_text("generated success", encoding="utf-8")
    indexer = SqliteCatalogIndexer(tmp_path / "data", CountingExtractor())
    _build(indexer, source, full=True)
    _build(indexer, source, full=False)
    before = sha256_file(indexer.active_path)
    backups_before = {path.name: sha256_file(path) for path in indexer.backup_root.glob("*.db")}
    (source / "new.txt").write_text("generated new content", encoding="utf-8")

    def fail_write(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic write failure")

    monkeypatch.setattr(indexer._writer, "upsert_file", fail_write)
    with pytest.raises(sqlite3.OperationalError, match="synthetic write failure"):
        _build(indexer, source, full=False)
    assert sha256_file(indexer.active_path) == before
    assert {
        path.name: sha256_file(path) for path in indexer.backup_root.glob("*.db")
    } == backups_before
    assert indexer.resume_path.is_file()


def test_catalog_rejects_missing_source_bad_id_and_invalid_database(tmp_path: Path) -> None:
    indexer = SqliteCatalogIndexer(tmp_path / "data")
    with pytest.raises(ValueError, match="nicht erreichbar"):
        _build(indexer, tmp_path / "missing", full=True)
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="source_id"):
        indexer.build(
            source,
            source_id="../bad",
            full_rebuild=True,
            settings=ServerSettings(),
            cancelled=lambda: False,
            progress=lambda *_args: None,
        )
    invalid = tmp_path / "invalid.db"
    invalid.write_text("not sqlite", encoding="utf-8")
    assert not indexer._is_v2_catalog(invalid)
    assert not indexer._is_v2_catalog(tmp_path / "absent.db")


def test_failed_atomic_activation_keeps_active_and_backups_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    document = source / "file.txt"
    document.write_text("one", encoding="utf-8")
    indexer = SqliteCatalogIndexer(tmp_path / "data")
    _build(indexer, source, full=True)
    _build(indexer, source, full=False)
    active_before = sha256_file(indexer.active_path)
    backups_before = {path.name: sha256_file(path) for path in indexer.backup_root.glob("*.db")}
    document.write_text("two", encoding="utf-8")
    real_replace = os.replace

    def fail_build_replace(source_path, destination_path):
        if (
            Path(source_path) == indexer.resume_path
            and Path(destination_path) == indexer.active_path
        ):
            raise OSError("activation failed")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr("papagui_server.adapters.catalog.os.replace", fail_build_replace)
    with pytest.raises(OSError, match="activation failed"):
        _build(indexer, source, full=False)
    assert sha256_file(indexer.active_path) == active_before
    assert {
        path.name: sha256_file(path) for path in indexer.backup_root.glob("*.db")
    } == backups_before
