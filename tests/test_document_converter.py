import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.services.document_converter import DocumentConverter


class DocumentConverterTests(unittest.TestCase):
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
