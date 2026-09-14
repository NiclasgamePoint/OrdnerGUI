from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from papagui_server.adapters.catalog_extraction import DocumentTextExtractor, ExternalCommandRunner
from papagui_server.domain.models import ServerSettings


def test_shared_runner_failure_status_is_thread_local():
    runner = ExternalCommandRunner()
    barrier = threading.Barrier(2)

    def run(fail):
        with runner.cancellation_scope(lambda: False):
            data = runner.run(
                [sys.executable, "-c", "raise SystemExit(2)" if fail else "print('okay')"],
                timeout=5,
            )
        barrier.wait(timeout=5)
        return data, runner.last_status

    with ThreadPoolExecutor(max_workers=2) as pool:
        good, bad = pool.submit(run, False), pool.submit(run, True)
        data, status = good.result(timeout=10)
        assert (data.splitlines(), status) == ([b"okay"], "ok")
        assert bad.result(timeout=10) == (b"", "error")
    assert runner.last_status == "ok"


@pytest.mark.parametrize("extension", ["pdf", "docx", "doc", "png"])
def test_document_cancellation_stops_running_pdf_office_legacy_and_ocr_tools(
    tmp_path: Path, monkeypatch, extension
):
    path = tmp_path / f"scan.{extension}"
    if extension == "png":
        from PIL import Image

        Image.new("RGB", (32, 32), "white").save(path)
    else:
        path.write_bytes(b"synthetic fixture")
    original_popen = subprocess.Popen
    started = threading.Event()
    cancelled = threading.Event()
    processes = []

    def synthetic_tool(command, **kwargs):
        if command[:2] == ["tesseract", "--list-langs"]:
            return original_popen([sys.executable, "-c", "print('deu')"], **kwargs)
        process = original_popen([sys.executable, "-c", "import time; time.sleep(90)"], **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", synthetic_tool)
    extractor = DocumentTextExtractor()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(extractor.extract_document, path, ServerSettings(), cancelled.is_set)
        try:
            assert started.wait(timeout=5)
            before = time.monotonic()
            cancelled.set()
            result = future.result(timeout=3)
            assert time.monotonic() - before < 3
            assert (result.status, result.reason) == ("partial", "cancelled")
            assert processes and all(process.poll() is not None for process in processes)
        finally:
            cancelled.set()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()


def test_cancellation_scope_does_not_leak_into_next_document(tmp_path: Path):
    extractor = DocumentTextExtractor()
    path = tmp_path / "note.txt"
    path.write_text("synthetic text")
    assert extractor.extract_document(path, ServerSettings(), lambda: True).reason == "cancelled"
    assert extractor.extract_document(path, ServerSettings()).text == "synthetic text"
    with extractor._runner.cancellation_scope(lambda: False):
        assert (
            extractor._runner.run([sys.executable, "-c", "print('next')"], timeout=5).splitlines() == [b"next"]
        )


def test_cancelling_one_document_does_not_cancel_another(tmp_path: Path, monkeypatch):
    extractor = DocumentTextExtractor()
    barrier = threading.Barrier(2)
    cancelled = threading.Event()
    original_popen = subprocess.Popen

    def synthetic_tool(command, **kwargs):
        failing = command[-1].endswith("cancel.doc")
        process = original_popen(
            [sys.executable, "-c", "import time;time.sleep(90)" if failing else "print('okay')"],
            **kwargs,
        )
        try:
            barrier.wait(timeout=5)
            cancelled.set()
        except BaseException:
            process.kill()
            process.wait()
            raise
        return process

    monkeypatch.setattr(subprocess, "Popen", synthetic_tool)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelled_result = pool.submit(
            extractor.extract_document, tmp_path / "cancel.doc", ServerSettings(), cancelled.is_set
        )
        successful_result = pool.submit(
            extractor.extract_document, tmp_path / "okay.doc", ServerSettings(), lambda: False
        )
        assert cancelled_result.result(timeout=5).reason == "cancelled"
        successful = successful_result.result(timeout=5)
        assert successful.status == "ok"
        assert successful.text.splitlines() == ["okay"]


def test_cancellable_runner_timeout_reaps_process():
    runner = ExternalCommandRunner()
    with runner.cancellation_scope(lambda: False):
        assert runner.run([sys.executable, "-c", "import time; time.sleep(90)"], timeout=1) == b""
    assert runner.last_status == "timeout"


@pytest.mark.skipif(sys.platform != "linux", reason="Checks process state via Linux procfs")
def test_cancellation_kills_parser_helper_children(tmp_path: Path, monkeypatch):
    runner = ExternalCommandRunner()
    pidfile = tmp_path / "helper.pid"
    cancelled = threading.Event()
    process_created = threading.Event()
    original_popen = subprocess.Popen
    processes = []

    def record_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        process_created.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", record_process)
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)']); "
        "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(90)"
    )

    def run():
        with runner.cancellation_scope(cancelled.is_set):
            return runner.run([sys.executable, "-c", code, str(pidfile)], timeout=90)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        try:
            assert process_created.wait(timeout=5)
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pidfile.exists()
            helper_pid = int(pidfile.read_text())
            cancelled.set()
            with pytest.raises(Exception) as error:
                future.result(timeout=3)
            assert type(error.value).__name__ == "_ExtractionCancelled"
            stat = Path(f"/proc/{helper_pid}/stat")

            def helper_running():
                try:
                    return stat.read_text().split()[2] not in {"Z", "X"}
                except (FileNotFoundError, ProcessLookupError):
                    return False

            deadline = time.monotonic() + 2
            while helper_running() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not helper_running()
        finally:
            cancelled.set()
            for process in processes:
                if process.poll() is None:
                    runner._stop(process)
