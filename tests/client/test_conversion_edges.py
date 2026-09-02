from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
import pytest

from papagui_client.gui.viewers.conversion_worker import FileConversionWorker
from papagui_client.viewers.conversion import DocumentPreviewConverter
from papagui_client.viewers.models import ConversionOutcome
from papagui_client.viewers.processes import PollingCommandRunner
from papagui_client.viewers.spreadsheets import SpreadsheetPreviewReader


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


class Tools:
    def __init__(self, **values):
        self.values = values

    def resolve(self, name):
        return self.values.get(name)


class Commands:
    def __init__(self, result=None, error=None, create_extension=None):
        self.result = result or subprocess.CompletedProcess([], 0, "", "")
        self.error = error
        self.create_extension = create_extension
        self.calls = []

    def run(self, command, **options):
        self.calls.append((command, options))
        if self.error:
            raise self.error
        if self.create_extension:
            output = Path(command[command.index("--outdir") + 1])
            (output / f"converted.{self.create_extension}").write_bytes(b"converted")
        return self.result


def test_legacy_doc_failure_nonexistent_and_python_read_error(tmp_path, monkeypatch):
    failed = Commands(subprocess.CompletedProcess([], 1, "", "failed"))
    converter = DocumentPreviewConverter(
        tmp_path / "cache", tools=Tools(catdoc=Path("/catdoc")), commands=failed
    )
    with patch.object(converter, "_extract_legacy_doc_python", return_value=("", "fallback")):
        with pytest.raises(RuntimeError, match="catdoc"):
            converter.extract_legacy_doc(tmp_path / "bad.doc")
    assert converter._extract_legacy_doc_python(tmp_path / "missing.doc") == (
        "",
        "Python-Fallback",
    )
    source = tmp_path / "source.doc"
    source.touch()
    with patch.object(Path, "read_bytes", side_effect=OSError("denied")):
        assert converter._extract_legacy_doc_python(source) == ("", "Python-Fallback")


def test_legacy_doc_olefile_streams_and_failures(tmp_path, monkeypatch):
    source = tmp_path / "source.doc"
    source.write_bytes(b"binary fallback readable")

    class OleDocument:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def listdir(self, **_kwargs):
            return [[], ["Ignored"], ["WordDocument"], ["Data"]]

        def openstream(self, stream):
            value = b"Useful content" if stream[-1] == "WordDocument" else b"!!!!"
            return SimpleNamespace(read=lambda: value)

    module = SimpleNamespace(isOleFile=lambda _path: True, OleFileIO=OleDocument)
    monkeypatch.setitem(sys.modules, "olefile", module)
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools())
    text, tool = converter._extract_legacy_doc_python(source)
    assert text == "Useful content"
    assert tool == "Python (olefile)"

    module.isOleFile = Mock(side_effect=ValueError("bad"))
    assert converter._extract_legacy_doc_python(source)[1] == "Python (binär)"
    module.isOleFile = lambda _path: True
    module.OleFileIO = Mock(side_effect=ValueError("bad stream"))
    assert converter._extract_legacy_doc_python(source)[1] == "Python (binär)"


def test_binary_string_extraction_filters_duplicates_noise_and_limits():
    extract = DocumentPreviewConverter._extract_strings_from_bytes
    assert extract(b"") == ""
    data = (
        "Unicode line".encode("utf-16-le")
        + b"\x00\x01ASCII readable line\r\nASCII readable line\r\n!!!!!"
    )
    text = extract(data)
    assert "Unicode line" in text
    assert text.count("ASCII readable line") == 1
    assert "!!!!!" not in text
    assert len(extract(b"First readable\nSecond readable", maximum=5)) == 5
    assert extract(b"First readable\nSecond readable", maximum=0) == ""


def test_generic_conversion_backend_order_success_and_failure(tmp_path):
    source = tmp_path / "book.xls"
    source.touch()
    rendered = tmp_path / "book.xlsx"
    rendered.touch()
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools())
    with patch.object(converter, "_convert_with_libreoffice", return_value=rendered):
        assert converter.convert(source, ".XLSX").tool == "LibreOffice"
    with (
        patch.object(converter, "_convert_with_libreoffice", return_value=None),
        patch.object(converter, "_convert_with_ms_office", return_value=rendered),
    ):
        assert converter.convert(source, "xlsx").tool == "MS Office"
    with (
        patch.object(converter, "_convert_with_libreoffice", return_value=None),
        patch.object(converter, "_convert_with_ms_office", return_value=None),
        pytest.raises(RuntimeError, match="Kein geeigneter Konverter"),
    ):
        converter.convert(source, "xlsx")


def test_word_preview_validation_backend_order_failure_and_cancel(tmp_path, monkeypatch):
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools())
    invalid = tmp_path / "letter.txt"
    invalid.touch()
    with pytest.raises(ValueError):
        converter.convert_word_to_pdf(invalid)
    source = tmp_path / "letter.docx"
    source.write_bytes(b"source")
    rendered = tmp_path / "rendered.pdf"
    rendered.write_bytes(b"pdf")

    monkeypatch.setattr("papagui_client.viewers.conversion.sys.platform", "win32")
    ms = Mock(return_value=rendered)
    libre = Mock(return_value=None)
    monkeypatch.setattr(converter, "_convert_with_ms_office", ms)
    monkeypatch.setattr(converter, "_convert_with_libreoffice", libre)
    assert converter.convert_word_to_pdf(source, prefer_ms_office=True).tool == "MS Office"
    ms.assert_called_once()

    (tmp_path / "cache").mkdir(exist_ok=True)
    for child in (tmp_path / "cache").iterdir():
        child.unlink()
    monkeypatch.setattr("papagui_client.viewers.conversion.sys.platform", "linux")
    libre.reset_mock()
    libre.return_value = None
    ms.return_value = None
    with pytest.raises(RuntimeError, match="Kein geeigneter Word-Konverter"):
        converter.convert_word_to_pdf(source)
    libre.assert_called_once()
    with pytest.raises(InterruptedError):
        converter.convert_word_to_pdf(source, should_cancel=lambda: True)


def test_libreoffice_conversion_is_isolated_and_cleans_failures(tmp_path):
    source = tmp_path / "letter.docx"
    source.touch()
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools())
    assert converter._convert_with_libreoffice(source, "pdf", None) is None

    commands = Commands(create_extension="pdf")
    converter = DocumentPreviewConverter(
        tmp_path / "cache", tools=Tools(libreoffice=Path("/office")), commands=commands
    )
    result = converter._convert_with_libreoffice(source, "pdf", None)
    assert result is not None and result.read_bytes() == b"converted"
    command, options = commands.calls[0]
    assert "--headless" in command
    assert options["environment"]["SAL_USE_VCLPLUGIN"] == "svp"
    runtime = Path(options["environment"]["XDG_RUNTIME_DIR"])
    assert runtime.stat().st_mode & 0o777 == 0o700
    converter.cleanup()

    failed = Commands(subprocess.CompletedProcess([], 1, "", "bad"))
    converter = DocumentPreviewConverter(
        tmp_path / "cache", tools=Tools(soffice=Path("/soffice")), commands=failed
    )
    assert converter._convert_with_libreoffice(source, "pdf", None) is None
    assert converter._temporary_directory is None


def test_ms_office_routing_and_capability_checks(tmp_path, monkeypatch):
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools())
    source = tmp_path / "book.xls"
    source.touch()
    assert converter._convert_with_ms_office(source, "xlsx", None) is None

    monkeypatch.setattr("papagui_client.viewers.conversion.sys.platform", "win32")
    with patch.object(converter, "_can_use_ms_excel", return_value=False):
        assert converter._convert_with_ms_office(source, "xlsx", None) is None
    with (
        patch.object(converter, "_can_use_ms_excel", return_value=True),
        patch.object(converter, "_run_excel_conversion", return_value=Path("done.xlsx")) as run,
    ):
        assert converter._convert_with_ms_office(source, "xlsx", None) == Path("done.xlsx")
        run.assert_called_once()
    word = tmp_path / "letter.doc"
    word.touch()
    with patch.object(converter, "_can_use_ms_word", return_value=False):
        assert converter._convert_with_ms_office(word, "pdf", None) is None
    with (
        patch.object(converter, "_can_use_ms_word", return_value=True),
        patch.object(converter, "_run_word_conversion", return_value=Path("done.pdf")) as run,
    ):
        assert converter._convert_with_ms_office(word, "pdf", None) == Path("done.pdf")
        run.assert_called_once()
    other = tmp_path / "slides.ppt"
    other.touch()
    assert converter._convert_with_ms_office(other, "pdf", None) is None
    with patch.object(converter, "_can_use_ms_word", return_value=True):
        assert converter._convert_with_ms_office(word, "xlsx", None) is None


def test_com_capability_probe_all_outcomes(tmp_path, monkeypatch):
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools())
    assert not converter._can_create_com_object("Word.Application", None)
    monkeypatch.setattr("papagui_client.viewers.conversion.sys.platform", "win32")
    assert not converter._can_create_com_object("Word.Application", None)

    commands = Commands(subprocess.CompletedProcess([], 0, "ok", ""))
    converter = DocumentPreviewConverter(
        tmp_path / "cache", tools=Tools(pwsh=Path("/pwsh")), commands=commands
    )
    assert converter._can_use_ms_excel(None)
    assert converter._can_use_ms_word(None)
    assert "word" in commands.calls[-1][0][-1].casefold()
    commands.result = subprocess.CompletedProcess([], 1, "no", "")
    assert not converter._can_create_com_object("Word.Application", None)
    commands.error = OSError("broken")
    assert not converter._can_create_com_object("Word.Application", None)
    commands.error = InterruptedError()
    with pytest.raises(InterruptedError):
        converter._can_create_com_object("Word.Application", None)


def test_powershell_scripts_execution_and_path_escaping(tmp_path):
    source = tmp_path / "customer's file.docx"
    source.touch()
    target = tmp_path / "out" / "file.pdf"
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools())
    assert converter._run_excel_conversion(source, target, None) is None
    assert converter._run_word_conversion(source, target, "pdf", None) is None
    assert converter._powershell_literal(source).count("''") == 1

    converter = DocumentPreviewConverter(
        tmp_path / "cache", tools=Tools(powershell=Path("/powershell"))
    )
    with patch.object(
        converter, "_run_powershell_conversion", return_value=target
    ) as run:
        assert converter._run_excel_conversion(source, target, None) == target
        assert "SaveAs" in run.call_args.args[1]
        assert converter._run_word_conversion(source, target, "pdf", None) == target
        assert "[ref]17" in run.call_args.args[1]
        assert converter._run_word_conversion(source, target, "txt", None) == target
        assert "[ref]2" in run.call_args.args[1]


def test_powershell_conversion_result_error_cancel_and_success(tmp_path):
    target = tmp_path / "output.pdf"
    power = Path("/powershell")
    converter = DocumentPreviewConverter(
        tmp_path / "cache", tools=Tools(), commands=Commands(error=InterruptedError())
    )
    converter._new_working_directory()
    with pytest.raises(InterruptedError):
        converter._run_powershell_conversion(power, "script", target, None)
    assert converter._temporary_directory is None

    converter._commands = Commands(error=OSError("failed"))
    converter._new_working_directory()
    assert converter._run_powershell_conversion(power, "script", target, None) is None
    converter._commands = Commands(subprocess.CompletedProcess([], 1, "", ""))
    assert converter._run_powershell_conversion(power, "script", target, None) is None
    converter._commands = Commands()
    assert converter._run_powershell_conversion(power, "script", target, None) is None
    target.write_bytes(b"pdf")
    assert converter._run_powershell_conversion(power, "script", target, None) == target


def test_converter_working_directory_cache_cleanup_and_context(tmp_path):
    converter = DocumentPreviewConverter(tmp_path / "cache", tools=Tools(foo=Path("/foo")))
    assert converter._resolve_first("missing", "foo") == Path("/foo")
    assert converter._resolve_first("missing") is None
    first = converter._new_working_directory()
    assert first.is_dir()
    second = converter._new_working_directory()
    assert not first.exists() and second.is_dir()
    converter.cleanup()
    converter.cleanup()

    cache_file = converter.cache_root / "value.pdf"
    cache_file.parent.mkdir(parents=True)
    cache_file.touch()
    converter.clear_cache()
    assert not converter.cache_root.exists()
    with converter as entered:
        assert entered is converter
        working = converter._new_working_directory()
    assert not working.exists()

    source = tmp_path / "source.pdf"
    source.write_bytes(b"value")
    target = tmp_path / "nested" / "target.pdf"
    assert converter._store_word_preview_cache(source, target) == target
    assert target.read_bytes() == b"value"
    with patch("papagui_client.viewers.conversion.shutil.copyfile", side_effect=OSError("full")):
        with pytest.raises(OSError):
            converter._store_word_preview_cache(source, tmp_path / "failure" / "target.pdf")
    assert not tuple(tmp_path.rglob(".*.tmp"))


class FakeConverter:
    def __init__(self, outcome=None, error=None):
        self.outcome = outcome or ConversionOutcome("convert", "fake", True, path=Path("x"))
        self.error = error
        self.cleaned = 0

    def _call(self):
        if self.error:
            raise self.error
        return self.outcome

    def convert(self, *_args, **_kwargs):
        return self._call()

    def extract_legacy_doc(self, *_args, **_kwargs):
        return self._call()

    def convert_word_to_pdf(self, *_args, **_kwargs):
        return self._call()

    def cleanup(self):
        self.cleaned += 1


@pytest.mark.parametrize(
    "operation",
    [
        FileConversionWorker.XLS_TO_XLSX,
        FileConversionWorker.EXTRACT_DOC,
        FileConversionWorker.WORD_TO_PDF,
    ],
)
def test_conversion_worker_routes_operations(application, operation):
    converter = FakeConverter()
    worker = FileConversionWorker(3, operation, Path("source"), lambda: converter)
    events = []
    worker.completed.connect(lambda *values: events.append(values))
    worker.run()
    assert events[0][:4] == (3, operation, converter.outcome, "")
    assert worker.take_converter() is converter
    assert worker.take_converter() is None


def test_conversion_worker_cancel_error_unknown_and_cleanup(application):
    converter = FakeConverter(error=InterruptedError())
    worker = FileConversionWorker(1, "unknown", Path("source"), lambda: converter)
    events = []
    worker.completed.connect(lambda *values: events.append(values))
    worker.run()
    assert "Unbekannte" in events[-1][3]

    converter.error = InterruptedError()
    worker.operation = FileConversionWorker.WORD_TO_PDF
    worker.run()
    assert events[-1][3] == "abgebrochen"
    converter.error = ValueError("broken")
    worker.run()
    assert events[-1][3] == "broken"
    converter.error = None
    with patch.object(worker, "isInterruptionRequested", return_value=True):
        worker.run()
    assert events[-1][3] == "abgebrochen"
    worker.converter = converter
    worker.cleanup()
    assert converter.cleaned == 1 and worker.converter is None


def test_polling_runner_timeout_and_force_kill(monkeypatch):
    import papagui_client.viewers.processes as processes

    class Process:
        returncode = None

        def __init__(self):
            self.calls = 0
            self.terminated = False
            self.killed = False

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls < 3:
                raise subprocess.TimeoutExpired("cmd", timeout)
            return ("", "")

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = Process()
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *_a, **_k: process)
    times = iter((0.0, 2.0))
    monkeypatch.setattr(processes.time, "monotonic", lambda: next(times))
    with pytest.raises(subprocess.TimeoutExpired):
        PollingCommandRunner().run(["cmd"], timeout=1)
    assert process.terminated

    class ForceKillProcess(Process):
        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("cmd", timeout)
            return ("", "")

    process = ForceKillProcess()
    PollingCommandRunner._terminate(process)
    assert process.terminated
    # The first communicate times out and forces kill; the second succeeds.
    assert process.killed


def test_spreadsheet_reader_xls_empty_xlsx_and_limits(tmp_path):
    with pytest.raises(ValueError):
        SpreadsheetPreviewReader(0, 1)
    with pytest.raises(ValueError):
        SpreadsheetPreviewReader(1, 0)

    class Sheet:
        nrows = 2
        ncols = 3

        def row_values(self, row, start, end):
            return [row, None, "x"][start:end]

    class Workbook:
        def __init__(self):
            self.released = 0

        def sheet_names(self):
            return ["Legacy"]

        def sheet_by_name(self, _name):
            return Sheet()

        def release_resources(self):
            self.released += 1

    workbook = Workbook()
    reader = SpreadsheetPreviewReader(1, 2)
    with patch.object(reader, "_open_xls", return_value=workbook):
        assert reader.sheet_names(tmp_path / "book.xls") == ("Legacy",)
        preview = reader.read_sheet(tmp_path / "book.xls", "Legacy")
    assert preview.values == ((0, None),)
    assert preview.truncated
    assert workbook.released == 2

    class EmptySheet:
        max_row = 0
        max_column = 0

        def iter_rows(self, **_kwargs):
            raise AssertionError("must not iterate")

    class Xlsx:
        sheetnames = ["Empty"]

        def __getitem__(self, _name):
            return EmptySheet()

        def close(self):
            self.closed = True

    workbook = Xlsx()
    with patch.object(reader, "_open_xlsx", return_value=workbook):
        assert reader.sheet_names(tmp_path / "empty.xlsx") == ("Empty",)
        assert reader.read_sheet(tmp_path / "empty.xlsx", "Empty").values == ()
    assert workbook.closed
