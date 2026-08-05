from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import doc_text_extractor, pdf_text_extractor, xls_text_extractor


class ExtractorEntrypointTests(unittest.TestCase):
    def test_doc_entrypoint_limits_output_and_suppresses_dialogs(self):
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["doc", "legacy.doc", "--maximum-characters", "4"]),
            patch.object(sys, "stdout", output),
            patch.object(doc_text_extractor, "suppress_windows_crash_dialogs") as suppress,
            patch.object(
                doc_text_extractor.DocumentConverter,
                "extract_legacy_doc",
                return_value="abcdef",
            ),
        ):
            self.assertEqual(doc_text_extractor.main(), 0)
        suppress.assert_called_once_with()
        self.assertEqual(output.getvalue(), "abcd")

    def test_pdf_entrypoint_skips_empty_pages_and_stops_at_limit(self):
        pages = [
            SimpleNamespace(extract_text=Mock(return_value=None)),
            SimpleNamespace(extract_text=Mock(return_value="abcd")),
            SimpleNamespace(extract_text=Mock(return_value="unused")),
        ]
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["pdf", "file.pdf", "--maximum-characters", "3"]),
            patch.object(sys, "stdout", output),
            patch.object(pdf_text_extractor, "PdfReader", return_value=SimpleNamespace(pages=pages)),
        ):
            self.assertEqual(pdf_text_extractor.main(), 0)
        self.assertEqual(output.getvalue(), "abc")
        pages[2].extract_text.assert_not_called()

        output = io.StringIO()
        complete_pages = [SimpleNamespace(extract_text=Mock(return_value="short"))]
        with (
            patch.object(sys, "argv", ["pdf", "file.pdf", "--maximum-characters", "20"]),
            patch.object(sys, "stdout", output),
            patch.object(
                pdf_text_extractor, "PdfReader",
                return_value=SimpleNamespace(pages=complete_pages),
            ),
        ):
            pdf_text_extractor.main()
        self.assertEqual(output.getvalue(), "short")

    def test_xls_extraction_collects_cells_and_releases_workbook(self):
        sheet = SimpleNamespace(
            name="Sheet",
            nrows=2,
            ncols=2,
            cell_value=lambda row, column: (("a", ""), ("b", "c"))[row][column],
        )
        workbook = SimpleNamespace(sheets=lambda: [sheet], release_resources=Mock())
        with patch.object(xls_text_extractor.xlrd, "open_workbook", return_value=workbook):
            result = xls_text_extractor.extract_xls_text(Path("file.xls"), 100)
        self.assertEqual(result, "Sheet\na\nb\tc")
        workbook.release_resources.assert_called_once_with()

        empty = SimpleNamespace(
            name="Empty", nrows=1, ncols=1,
            cell_value=lambda _row, _column: "",
        )
        workbook = SimpleNamespace(sheets=lambda: [empty], release_resources=Mock())
        with patch.object(xls_text_extractor.xlrd, "open_workbook", return_value=workbook):
            self.assertEqual(xls_text_extractor.extract_xls_text(Path("empty.xls"), 10), "Empty")

    def test_xls_extraction_returns_early_at_character_limit(self):
        sheet = SimpleNamespace(
            name="Sheet",
            nrows=2,
            ncols=1,
            cell_value=lambda row, _column: ("abcd", "unused")[row],
        )
        workbook = SimpleNamespace(sheets=lambda: [sheet], release_resources=Mock())
        with patch.object(xls_text_extractor.xlrd, "open_workbook", return_value=workbook):
            self.assertEqual(xls_text_extractor.extract_xls_text(Path("x"), 3), "She")
        workbook.release_resources.assert_called_once_with()

    def test_xls_entrypoint_writes_extracted_text(self):
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["xls", "file.xls", "--maximum-characters", "5"]),
            patch.object(sys, "stdout", output),
            patch.object(xls_text_extractor, "suppress_windows_crash_dialogs"),
            patch.object(xls_text_extractor, "extract_xls_text", return_value="value"),
        ):
            self.assertEqual(xls_text_extractor.main(), 0)
        self.assertEqual(output.getvalue(), "value")
