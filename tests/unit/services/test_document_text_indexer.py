from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.config import IndexOptions
from app.services.document_text_indexer import DocumentTextIndexer
from app.services.extraction_models import ExtractionResult
from tests.base.test_case import PapaGuiTestCase


class FakeTools:
    def __init__(self, values=None):
        self.values = values or {}

    def resolve(self, name):
        return self.values.get(name)


class DocumentTextIndexerTests(PapaGuiTestCase):
    def indexer(self, **changes) -> DocumentTextIndexer:
        options = IndexOptions(**changes)
        return DocumentTextIndexer(options, FakeTools())

    def test_extract_maps_stat_size_status_timeout_and_errors(self):
        indexer = self.indexer(max_file_size_mb=1)
        missing = self.temp_path / "missing.txt"
        result = indexer.extract(missing)
        self.assertEqual(result.status, "error")

        large = self.make_file("large.txt", "x")
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_size=2 * 1024 * 1024)):
            result = indexer.extract(large)
        self.assertEqual(result.status, "skipped_large")

        for extracted, expected in (
            (ExtractionResult(text="text", status="pending"), "success"),
            (ExtractionResult(text=" ", status="pending"), "empty"),
            (ExtractionResult(status="encrypted"), "encrypted"),
        ):
            with patch.object(indexer, "_extract", return_value=extracted):
                self.assertEqual(indexer.extract(large).status, expected)
        with patch.object(indexer, "_extract", side_effect=TimeoutError("slow")):
            self.assertEqual(indexer.extract(large).status, "timeout")
        with patch.object(indexer, "_extract", side_effect=RuntimeError("Password required")):
            self.assertEqual(indexer.extract(large).status, "encrypted")
        with patch.object(indexer, "_extract", side_effect=RuntimeError("broken")):
            self.assertEqual(indexer.extract(large).status, "error")

    def test_extract_dispatches_every_supported_format(self):
        indexer = self.indexer(max_extracted_characters=4)
        text = self.make_file("file.txt", "abcdef")
        self.assertEqual(indexer._extract(text, "txt").text, "abcd")
        with patch.object(indexer, "_pdf_result", return_value=ExtractionResult(text="pdf")):
            self.assertEqual(indexer._extract(text, "pdf").text, "pdf")
        with patch.object(indexer, "_docx", return_value="docx"):
            self.assertEqual(indexer._extract(text, "docx").parser, "python-docx")
        for extension in ("doc", "xls"):
            with patch.object(indexer, "_legacy", return_value=extension):
                self.assertEqual(indexer._extract(text, extension).text, extension)
        with patch.object(indexer, "_xlsx", return_value="xlsx"):
            self.assertEqual(indexer._extract(text, "xlsx").parser, "openpyxl")
        self.assertEqual(indexer._extract(text, "unknown").parser, "unsupported")

    def test_pdf_page_count_handles_tools_errors_and_output(self):
        indexer = self.indexer()
        self.assertIsNone(indexer._pdf_page_count(Path("x"), 9999999999))
        indexer.tools = FakeTools({"pdfinfo": Path("pdfinfo")})
        with patch.object(indexer, "_run", side_effect=OSError):
            self.assertIsNone(indexer._pdf_page_count(Path("x"), 9999999999))
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertIsNone(indexer._pdf_page_count(Path("x"), 9999999999))
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 0, "no pages", "")):
            self.assertIsNone(indexer._pdf_page_count(Path("x"), 9999999999))
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 0, "Pages: 12\n", "")):
            self.assertEqual(indexer._pdf_page_count(Path("x"), 9999999999), 12)
        with patch("app.services.document_text_indexer.time.monotonic", return_value=2):
            self.assertEqual(indexer._remaining(3), 1)
            with self.assertRaises(TimeoutError):
                indexer._remaining(2)

    def test_pdf_fallback_reports_reader_failure(self):
        indexer = self.indexer()
        with (
            patch.object(indexer, "_pdf_page_count", return_value=None),
            patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 1, "", "reader failed")),
            self.assertRaisesRegex(RuntimeError, "reader failed"),
        ):
            indexer._pdf(Path("x.pdf"))

    def test_docx_and_xlsx_extract_tables_and_close_resources(self):
        indexer = self.indexer(max_extracted_characters=100)
        document = SimpleNamespace(
            paragraphs=[SimpleNamespace(text="paragraph"), SimpleNamespace(text="")],
            tables=[SimpleNamespace(rows=[SimpleNamespace(cells=[SimpleNamespace(text="a"), SimpleNamespace(text="b")])])],
        )
        with patch("app.services.document_text_indexer.Document", return_value=document):
            self.assertEqual(indexer._docx(Path("x")), "paragraph\na\tb")
        empty_document = SimpleNamespace(
            paragraphs=[],
            tables=[SimpleNamespace(rows=[SimpleNamespace(cells=[SimpleNamespace(text="")])])],
        )
        with patch("app.services.document_text_indexer.Document", return_value=empty_document):
            self.assertEqual(indexer._docx(Path("x")), "")
        indexer.options = IndexOptions(max_extracted_characters=2)
        with patch("app.services.document_text_indexer.Document", return_value=document):
            self.assertEqual(indexer._docx(Path("x")), "pa")

        worksheet = SimpleNamespace(title="Sheet", iter_rows=lambda values_only: [(1, None, "x")])
        workbook = SimpleNamespace(worksheets=[worksheet], close=Mock())
        indexer.options = IndexOptions(max_extracted_characters=100)
        with patch("app.services.document_text_indexer.openpyxl.load_workbook", return_value=workbook):
            self.assertEqual(indexer._xlsx(Path("x")), "Sheet\n1\tx")
        workbook.close.assert_called_once_with()
        empty_worksheet = SimpleNamespace(title="Empty", iter_rows=lambda values_only: [(None,)])
        empty_workbook = SimpleNamespace(worksheets=[empty_worksheet], close=Mock())
        with patch("app.services.document_text_indexer.openpyxl.load_workbook", return_value=empty_workbook):
            self.assertEqual(indexer._xlsx(Path("x")), "Empty")
        indexer.options = IndexOptions(max_extracted_characters=1)
        workbook.close.reset_mock()
        with patch("app.services.document_text_indexer.openpyxl.load_workbook", return_value=workbook):
            self.assertEqual(indexer._xlsx(Path("x")), "S")
        workbook.close.assert_called_once_with()

    def test_legacy_reports_timeout_process_failure_and_limits_output(self):
        indexer = self.indexer(max_extracted_characters=3)
        with patch.object(indexer, "_run", side_effect=TimeoutError("slow")):
            with self.assertRaisesRegex(TimeoutError, "XLS-Zeitlimit"):
                indexer._legacy(Path("x.xls"), "xls")
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 1, "", "bad")):
            with self.assertRaisesRegex(RuntimeError, "bad"):
                indexer._legacy(Path("x.doc"), "doc")
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 0, "abcdef", "")):
            self.assertEqual(indexer._legacy(Path("x.doc"), "doc"), "abc")

    def test_ocr_range_render_language_and_page_processing(self):
        indexer = self.indexer()
        self.assertEqual(indexer._ocr_range(Path("x"), self.temp_path, 2, 1, Path("ppm"), Path("ocr"), 9999999999), [])
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(indexer._ocr_range(Path("x"), self.temp_path, 1, 1, Path("ppm"), Path("ocr"), 9999999999), [])
        (self.temp_path / "stage-1-1.jpg").write_bytes(b"image")
        results = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "eng\ntext", ""),
            subprocess.CompletedProcess([], 0, "recognized", ""),
        ]
        with patch.object(indexer, "_run", side_effect=results):
            self.assertEqual(
                indexer._ocr_range(Path("x"), self.temp_path, 1, 1, Path("ppm"), Path("ocr"), 9999999999),
                ["recognized"],
            )
        indexer._ocr_language = None
        with patch.object(indexer, "_run", side_effect=OSError):
            self.assertEqual(indexer._get_ocr_language(Path("ocr")), "eng")
        indexer._ocr_language = None
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 0, "langs\ndeu\neng\n", "")):
            self.assertEqual(indexer._get_ocr_language(Path("ocr")), "deu+eng")
        indexer._ocr_language = None
        with patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 0, "langs\ndeu\n", "")):
            self.assertEqual(indexer._get_ocr_language(Path("ocr")), "deu")
        with patch.object(indexer, "_run") as run:
            self.assertEqual(indexer._get_ocr_language(Path("ocr")), "deu")
            run.assert_not_called()

    def test_ocr_pdf_handles_missing_tools_and_two_stages(self):
        indexer = self.indexer(ocr_max_pages=1, ocr_extended_max_pages=2, ocr_extension_threshold=5)
        self.assertEqual(indexer._ocr_pdf(Path("x")), ("", 0, None))
        indexer.tools = FakeTools({"pdftoppm": Path("ppm"), "tesseract": Path("ocr")})
        with patch.object(indexer, "_ocr_range", side_effect=[["a"], ["bc"]]) as ocr_range:
            self.assertEqual(indexer._ocr_pdf(Path("x")), ("a\nbc", 2, None))
            self.assertEqual(ocr_range.call_count, 2)
        with patch.object(indexer, "_ocr_range", return_value=["enough"] ) as ocr_range:
            self.assertEqual(indexer._ocr_pdf(Path("x"))[1], 1)
            ocr_range.assert_called_once()

    def test_find_tesseract_checks_resolver_and_windows_locations(self):
        indexer = self.indexer()
        indexer.tools = FakeTools({"tesseract": Path("resolved")})
        self.assertEqual(indexer._find_tesseract(), Path("resolved"))
        indexer.tools = FakeTools()
        with patch("app.services.document_text_indexer.sys.platform", "linux"):
            self.assertIsNone(indexer._find_tesseract())
        with (
            patch("app.services.document_text_indexer.sys.platform", "win32"),
            patch.dict("app.services.document_text_indexer.os.environ", {"PROGRAMFILES": "C:/Programs"}, clear=True),
            patch.object(Path, "exists", return_value=True),
        ):
            self.assertEqual(indexer._find_tesseract().name, "tesseract.exe")
        with (
            patch("app.services.document_text_indexer.sys.platform", "win32"),
            patch.dict("app.services.document_text_indexer.os.environ", {"PROGRAMFILES": "C:/Programs"}, clear=True),
            patch.object(Path, "exists", return_value=False),
        ):
            self.assertIsNone(indexer._find_tesseract())
        with (
            patch("app.services.document_text_indexer.sys.platform", "win32"),
            patch.dict("app.services.document_text_indexer.os.environ", {}, clear=True),
        ):
            self.assertIsNone(indexer._find_tesseract())

    def test_run_sets_cwd_windows_flags_and_translates_timeout(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch("app.services.document_text_indexer.subprocess.run", return_value=completed) as run:
            self.assertIs(DocumentTextIndexer._run(["tool"], timeout=0, cwd=Path("work")), completed)
        self.assertEqual(run.call_args.kwargs["timeout"], 0.1)
        self.assertEqual(run.call_args.kwargs["cwd"], "work")
        with (
            patch("app.services.document_text_indexer.sys.platform", "win32"),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch("app.services.document_text_indexer.subprocess.run", return_value=completed) as run,
        ):
            DocumentTextIndexer._run(["tool"], timeout=1)
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
        with patch("app.services.document_text_indexer.subprocess.run", side_effect=subprocess.TimeoutExpired(["tool"], 1)):
            with self.assertRaisesRegex(TimeoutError, "tool"):
                DocumentTextIndexer._run(["tool"], timeout=1)
