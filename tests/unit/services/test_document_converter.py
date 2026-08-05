import sys
import time
import unittest
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.services.document_converter import DocumentConverter


class DocumentConverterTests(unittest.TestCase):
    def test_operation_state_can_be_set_and_reset(self):
        converter = DocumentConverter()
        converter._set_last_operation(converted=True, tool="Tool", action="test")
        state = converter.get_last_operation()
        state["tool"] = "mutated"
        self.assertEqual(converter.get_last_operation()["tool"], "Tool")
        converter.reset_last_operation()
        self.assertEqual(
            converter.get_last_operation(),
            {"converted": False, "tool": "Direkt", "action": "none"},
        )

    def test_binary_python_fallback_extracts_ascii_utf16_and_filters_noise(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.doc"
            path.write_bytes(
                "Unicode line".encode("utf-16-le")
                + b"\x00\x01Readable ASCII line\r\n!!!!!\r\nReadable ASCII line"
            )
            converter = DocumentConverter()
            text, tool = converter._extract_legacy_doc_python(path)
        self.assertEqual(tool, "Python (binär)")
        self.assertIn("Readable ASCII line", text)
        self.assertNotIn("!!!!!", text)
        self.assertEqual(DocumentConverter._extract_strings_from_bytes(b""), "")

    def test_python_fallback_handles_missing_and_unreadable_files(self):
        converter = DocumentConverter()
        self.assertEqual(
            converter._extract_legacy_doc_python(Path("missing.doc")),
            ("", "Python-Fallback"),
        )
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_bytes", side_effect=OSError("denied")),
        ):
            self.assertEqual(
                converter._extract_legacy_doc_python(Path("present.doc")),
                ("", "Python-Fallback"),
            )

    def test_python_fallback_reads_supported_ole_streams_and_survives_ole_errors(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.doc"
            path.write_bytes(b"binary fallback")
            ole = Mock()
            ole.__enter__ = Mock(return_value=ole)
            ole.__exit__ = Mock(return_value=False)
            ole.listdir.return_value = [(), ("Ignored",), ("WordDocument",)]
            ole.openstream.return_value.read.return_value = b"Readable stream text"
            fake_olefile = Mock()
            fake_olefile.isOleFile.return_value = True
            fake_olefile.OleFileIO.return_value = ole
            with patch("app.services.document_converter.olefile", fake_olefile):
                self.assertEqual(
                    DocumentConverter()._extract_legacy_doc_python(path),
                    ("Readable stream text", "Python (olefile)"),
                )
            fake_olefile.OleFileIO.side_effect = OSError("broken")
            with patch("app.services.document_converter.olefile", fake_olefile):
                text, tool = DocumentConverter()._extract_legacy_doc_python(path)
            self.assertEqual(tool, "Python (binär)")
            self.assertIn("binary fallback", text)
            fake_olefile.OleFileIO.side_effect = None
            fake_olefile.OleFileIO.return_value = ole
            ole.openstream.return_value.read.return_value = b"!!!!!"
            with patch("app.services.document_converter.olefile", fake_olefile):
                text, tool = DocumentConverter()._extract_legacy_doc_python(path)
            self.assertEqual(tool, "Python (binär)")

    def test_binary_extraction_handles_short_lines_and_size_limit(self):
        text = DocumentConverter._extract_strings_from_bytes(b"aa\nbbb\nReadable")
        self.assertNotIn("aa", text)
        self.assertIn("bbb", text)
        huge = (b"A" * 2_000_000) + b"\n"
        self.assertEqual(len(DocumentConverter._extract_strings_from_bytes(huge)), 2_000_000)

    def test_extract_legacy_doc_uses_external_tool_then_python_fallback(self):
        converter = DocumentConverter()
        success = subprocess.CompletedProcess([], 0, "external", "")
        with (
            patch("app.services.document_converter.shutil.which", return_value="/bin/catdoc"),
            patch.object(converter, "_run_command", return_value=success),
        ):
            self.assertEqual(converter.extract_legacy_doc(Path("file.doc")), "external")
        failed = subprocess.CompletedProcess([], 1, "", "failed")
        with (
            patch("app.services.document_converter.shutil.which", return_value="/bin/catdoc"),
            patch.object(converter, "_run_command", return_value=failed),
            patch.object(converter, "_extract_legacy_doc_python", return_value=("fallback", "Python")),
        ):
            self.assertEqual(converter.extract_legacy_doc(Path("file.doc")), "fallback")
        with (
            patch("app.services.document_converter.shutil.which", return_value="/bin/catdoc"),
            patch.object(converter, "_run_command", return_value=failed),
            patch.object(converter, "_extract_legacy_doc_python", return_value=("", "Python")),
            self.assertRaisesRegex(RuntimeError, "failed"),
        ):
            converter.extract_legacy_doc(Path("file.doc"))

    def test_extract_legacy_doc_without_tool_uses_python_or_reports_missing(self):
        converter = DocumentConverter()
        with (
            patch("app.services.document_converter.shutil.which", return_value=None),
            patch.object(converter, "_extract_legacy_doc_python", return_value=("text", "Python")),
        ):
            self.assertEqual(converter.extract_legacy_doc(Path("file.doc")), "text")
        with (
            patch("app.services.document_converter.shutil.which", return_value=None),
            patch.object(converter, "_extract_legacy_doc_python", return_value=("", "Python")),
            self.assertRaisesRegex(RuntimeError, "kein externer Konverter"),
        ):
            converter.extract_legacy_doc(Path("file.doc"))

    def test_generic_convert_uses_both_backends_and_cancellation(self):
        converter = DocumentConverter()
        target = Path("converted.pdf")
        with patch.object(converter, "_convert_with_libreoffice", return_value=target):
            self.assertEqual(converter.convert(Path("x.doc"), ".PDF"), target)
        with (
            patch.object(converter, "_convert_with_libreoffice", return_value=None),
            patch.object(converter, "_convert_with_ms_office", return_value=target),
        ):
            self.assertEqual(converter.convert(Path("x.doc"), "pdf"), target)
        with (
            patch.object(converter, "_convert_with_libreoffice", return_value=None),
            self.assertRaises(InterruptedError),
        ):
            converter.convert(Path("x.doc"), "pdf", should_cancel=lambda: True)
        with (
            patch.object(converter, "_convert_with_libreoffice", return_value=None),
            patch.object(converter, "_convert_with_ms_office", return_value=None),
            self.assertRaises(RuntimeError),
        ):
            converter.convert(Path("x.doc"), "pdf")

    def test_word_preview_validation_cancellation_fallback_and_failure(self):
        converter = DocumentConverter()
        with self.assertRaises(ValueError):
            converter.convert_word_to_pdf(Path("sheet.xlsx"))
        with TemporaryDirectory() as directory:
            source = Path(directory) / "x.docx"
            source.write_bytes(b"x")
            with (
                patch("app.services.document_converter.sys.platform", "win32"),
                patch.object(converter, "_convert_with_ms_office", return_value=None),
                self.assertRaises(InterruptedError),
            ):
                converter.convert_word_to_pdf(source, should_cancel=lambda: True)
            with (
                patch("app.services.document_converter.sys.platform", "linux"),
                patch.object(converter, "_convert_with_libreoffice", return_value=None),
                self.assertRaises(RuntimeError),
            ):
                converter.convert_word_to_pdf(source)
            with (
                patch("app.services.document_converter.sys.platform", "linux"),
                patch.object(converter, "_convert_with_libreoffice", return_value=None),
                self.assertRaises(InterruptedError),
            ):
                converter.convert_word_to_pdf(source, should_cancel=lambda: True)
            target = Path(directory) / "converted.pdf"
            target.write_bytes(b"pdf")
            cache = Path(directory) / "cache.pdf"
            with (
                patch("app.services.document_converter.sys.platform", "win32"),
                patch.object(converter, "_convert_with_libreoffice", return_value=None),
                patch.object(converter, "_convert_with_ms_office", return_value=target),
                patch.object(converter, "_word_preview_cache_path", return_value=cache),
            ):
                self.assertEqual(
                    converter.convert_word_to_pdf(source, prefer_ms_office=False), cache
                )
            cache.unlink()
            with (
                patch("app.services.document_converter.sys.platform", "win32"),
                patch.object(converter, "_convert_with_libreoffice", return_value=None),
                patch.object(converter, "_convert_with_ms_office", return_value=None),
                patch.object(converter, "_word_preview_cache_path", return_value=cache),
                self.assertRaises(RuntimeError),
            ):
                converter.convert_word_to_pdf(source, prefer_ms_office=False)

    def test_cache_path_changes_with_source_metadata(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "x.docx"
            source.write_bytes(b"one")
            converter = DocumentConverter()
            with patch.object(converter, "WORD_PREVIEW_CACHE_DIR", Path(directory) / "cache"):
                first = converter._word_preview_cache_path(source)
                source.write_bytes(b"different")
                second = converter._word_preview_cache_path(source)
        self.assertNotEqual(first, second)

    def test_libreoffice_conversion_missing_failed_and_successful(self):
        converter = DocumentConverter()
        with patch("app.services.document_converter.shutil.which", return_value=None):
            self.assertIsNone(converter._convert_with_libreoffice(Path("x"), "pdf", None))
        completed = subprocess.CompletedProcess([], 0, "", "")

        def run_and_create(_command, **kwargs):
            output = Path(converter._temporary_directory.name)
            (output / "x.pdf").write_bytes(b"pdf")
            return completed

        with (
            patch("app.services.document_converter.shutil.which", return_value="libreoffice"),
            patch.object(converter, "_run_command", side_effect=run_and_create),
        ):
            result = converter._convert_with_libreoffice(Path("x.doc"), "pdf", None)
            self.assertTrue(result.is_file())
        converter.cleanup()
        with (
            patch("app.services.document_converter.shutil.which", return_value="libreoffice"),
            patch.object(converter, "_run_command", return_value=subprocess.CompletedProcess([], 1, "", "bad")),
        ):
            self.assertIsNone(converter._convert_with_libreoffice(Path("x.doc"), "pdf", None))

    def test_ms_office_capability_checks_all_outcomes(self):
        converter = DocumentConverter()
        for method_name in ("_can_use_ms_office", "_can_use_ms_word"):
            method = getattr(converter, method_name)
            with patch("app.services.document_converter.sys.platform", "linux"):
                self.assertFalse(method(None))
            with (
                patch("app.services.document_converter.sys.platform", "win32"),
                patch("app.services.document_converter.shutil.which", return_value=None),
            ):
                self.assertFalse(method(None))
            with (
                patch("app.services.document_converter.sys.platform", "win32"),
                patch("app.services.document_converter.shutil.which", return_value="powershell"),
                patch.object(converter, "_run_command", side_effect=InterruptedError),
                self.assertRaises(InterruptedError),
            ):
                method(None)
            with (
                patch("app.services.document_converter.sys.platform", "win32"),
                patch("app.services.document_converter.shutil.which", return_value="powershell"),
                patch.object(converter, "_run_command", return_value=subprocess.CompletedProcess([], 0, "OK", "")),
            ):
                self.assertTrue(method(None))
            with (
                patch("app.services.document_converter.sys.platform", "win32"),
                patch("app.services.document_converter.shutil.which", return_value="powershell"),
                patch.object(converter, "_run_command", side_effect=OSError),
            ):
                self.assertFalse(method(None))

    def test_ms_dispatch_rejects_unsupported_conversion(self):
        converter = DocumentConverter()
        with patch.object(converter, "_can_use_ms_office", return_value=True):
            self.assertIsNone(converter._convert_with_ms_office(Path("x.ppt"), "pdf", None))

    def test_ms_dispatch_routes_excel_and_word(self):
        converter = DocumentConverter()
        with (
            patch.object(converter, "_can_use_ms_office", return_value=True),
            patch.object(converter, "_ms_excel_convert", return_value=Path("excel.xlsx")) as excel,
        ):
            self.assertEqual(
                converter._convert_with_ms_office(Path("x.xls"), "xlsx", None),
                Path("excel.xlsx"),
            )
            excel.assert_called_once()
        converter.cleanup()
        with (
            patch.object(converter, "_can_use_ms_word", return_value=True),
            patch.object(converter, "_ms_word_convert", return_value=Path("word.pdf")) as word,
        ):
            self.assertEqual(
                converter._convert_with_ms_office(Path("x.doc"), "pdf", None),
                Path("word.pdf"),
            )
            word.assert_called_once()
        converter.cleanup()
        with patch.object(converter, "_can_use_ms_word", return_value=False):
            self.assertIsNone(converter._convert_with_ms_office(Path("x.doc"), "pdf", None))
        with patch.object(converter, "_can_use_ms_office", return_value=False):
            self.assertIsNone(converter._convert_with_ms_office(Path("x.xls"), "xlsx", None))

    def test_ms_excel_and_word_converters_cover_success_failure_and_errors(self):
        converter = DocumentConverter()
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source's.docx"
            source.write_bytes(b"source")
            for method_name, extension in (("_ms_excel_convert", "xlsx"), ("_ms_word_convert", "pdf"), ("_ms_word_convert", "txt")):
                method = getattr(converter, method_name)
                target = Path(directory) / f"target.{extension}"
                arguments = (source, target, None) if method_name == "_ms_excel_convert" else (source, target, extension, None)
                with patch("app.services.document_converter.shutil.which", return_value=None):
                    self.assertIsNone(method(*arguments))

                def successful_run(*_args, **_kwargs):
                    target.write_bytes(b"result")
                    return subprocess.CompletedProcess([], 0, "", "")

                with (
                    patch("app.services.document_converter.shutil.which", return_value="powershell"),
                    patch.object(converter, "_run_command", side_effect=successful_run),
                ):
                    self.assertEqual(method(*arguments), target)
                target.unlink()
                with (
                    patch("app.services.document_converter.shutil.which", return_value="powershell"),
                    patch.object(converter, "_run_command", return_value=subprocess.CompletedProcess([], 1, "", "bad")),
                ):
                    self.assertIsNone(method(*arguments))
                with (
                    patch("app.services.document_converter.shutil.which", return_value="powershell"),
                    patch.object(converter, "_run_command", side_effect=OSError("failed")),
                ):
                    self.assertIsNone(method(*arguments))
                with (
                    patch("app.services.document_converter.shutil.which", return_value="powershell"),
                    patch.object(converter, "_run_command", side_effect=InterruptedError),
                    self.assertRaises(InterruptedError),
                ):
                    method(*arguments)

    def test_run_command_returns_and_times_out(self):
        converter = DocumentConverter()
        process = Mock(returncode=0)
        process.communicate.return_value = ("out", "err")
        with patch("app.services.document_converter.subprocess.Popen", return_value=process):
            result = converter._run_command(["tool"], timeout=1)
        self.assertEqual((result.stdout, result.stderr), ("out", "err"))

        timed_out = Mock()
        timed_out.communicate.side_effect = subprocess.TimeoutExpired([], 1)
        with (
            patch("app.services.document_converter.subprocess.Popen", return_value=timed_out),
            patch("app.services.document_converter.time.monotonic", side_effect=[0, 2]),
            patch.object(converter, "_terminate_process") as terminate,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            converter._run_command(["tool"], timeout=1)
        terminate.assert_called_once_with(timed_out)

        eventually_finishes = Mock(returncode=0)
        eventually_finishes.communicate.side_effect = [
            subprocess.TimeoutExpired([], 1),
            ("done", ""),
        ]
        with (
            patch("app.services.document_converter.subprocess.Popen", return_value=eventually_finishes),
            patch("app.services.document_converter.time.monotonic", side_effect=[0, 0.5]),
        ):
            result = converter._run_command(["tool"], timeout=1)
        self.assertEqual(result.stdout, "done")

    def test_terminate_process_escalates_to_kill(self):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired([], 1), ("", "")]
        DocumentConverter._terminate_process(process)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

    def test_cleanup_releases_temporary_directory(self):
        converter = DocumentConverter()
        temporary = Mock()
        converter._temporary_directory = temporary
        converter.cleanup()
        temporary.cleanup.assert_called_once_with()
        self.assertIsNone(converter._temporary_directory)
    def test_external_process_can_be_cancelled_promptly(self):
        converter = DocumentConverter()
        started = time.monotonic()

        with self.assertRaises(InterruptedError):
            converter._run_command(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(5)",
                ],
                timeout=10,
                should_cancel=lambda: time.monotonic() - started > 0.05,
            )

        self.assertLess(time.monotonic() - started, 2)

    def test_word_preview_prefers_ms_office_on_windows(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "test.docx"
            target = Path(directory) / "test.pdf"
            source.write_bytes(b"docx")
            target.write_bytes(b"%PDF")
            cache_path = Path(directory) / "cache.pdf"
            converter = DocumentConverter()
            converter._convert_with_ms_office = Mock(return_value=target)
            converter._convert_with_libreoffice = Mock(return_value=None)
            converter._word_preview_cache_path = Mock(return_value=cache_path)

            with patch("app.services.document_converter.sys.platform", "win32"):
                result = converter.convert_word_to_pdf(source)

            self.assertEqual(result, cache_path)
            self.assertTrue(cache_path.exists())
            converter._convert_with_ms_office.assert_called_once()
            converter._convert_with_libreoffice.assert_not_called()
            self.assertEqual(converter.get_last_operation()["tool"], "MS Office")

    def test_word_preview_uses_libreoffice_when_ms_office_is_unavailable(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "test.docx"
            target = Path(directory) / "test.pdf"
            source.write_bytes(b"docx")
            target.write_bytes(b"%PDF")
            cache_path = Path(directory) / "cache.pdf"
            converter = DocumentConverter()
            converter._convert_with_ms_office = Mock(return_value=None)
            converter._convert_with_libreoffice = Mock(return_value=target)
            converter._word_preview_cache_path = Mock(return_value=cache_path)

            with patch("app.services.document_converter.sys.platform", "win32"):
                result = converter.convert_word_to_pdf(source)

            self.assertEqual(result, cache_path)
            self.assertTrue(cache_path.exists())
            converter._convert_with_ms_office.assert_called_once()
            converter._convert_with_libreoffice.assert_called_once()
            self.assertEqual(converter.get_last_operation()["tool"], "LibreOffice")

    def test_word_preview_uses_cached_pdf_without_converter(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "test.docx"
            cache_path = Path(directory) / "cache.pdf"
            source.write_bytes(b"docx")
            cache_path.write_bytes(b"%PDF")
            converter = DocumentConverter()
            converter._convert_with_ms_office = Mock(return_value=None)
            converter._convert_with_libreoffice = Mock(return_value=None)
            converter._word_preview_cache_path = Mock(return_value=cache_path)

            result = converter.convert_word_to_pdf(source)

            self.assertEqual(result, cache_path)
            converter._convert_with_ms_office.assert_not_called()
            converter._convert_with_libreoffice.assert_not_called()
            self.assertEqual(converter.get_last_operation()["tool"], "Cache")

    def test_clear_word_preview_cache_removes_cache_directory(self):
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "preview_cache" / "word"
            cache_file = cache_dir / "cached.pdf"
            cache_dir.mkdir(parents=True)
            cache_file.write_bytes(b"%PDF")

            with patch.object(DocumentConverter, "WORD_PREVIEW_CACHE_DIR", cache_dir):
                DocumentConverter.clear_word_preview_cache()

            self.assertFalse(cache_dir.exists())


if __name__ == "__main__":
    unittest.main()
