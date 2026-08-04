from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import re
import subprocess
import sys
import time
from tempfile import TemporaryDirectory

import openpyxl
from docx import Document

from app.core.config import IndexOptions
from app.services.extraction_models import ExtractionResult
from app.services.index_tool_resolver import IndexToolResolver


class DocumentTextIndexer:
    """Extract bounded searchable text and report parser-independent metrics."""

    LEGACY_XLS_TIMEOUT_SECONDS = 15
    LEGACY_DOC_TIMEOUT_SECONDS = 20
    TEXT_TYPES = {"txt", "csv", "md", "log", "json", "xml", "yaml", "yml", "ini"}

    def __init__(
        self,
        options: IndexOptions,
        tool_resolver: IndexToolResolver | None = None,
    ):
        self.options = options
        self.tools = tool_resolver or IndexToolResolver()
        self._ocr_language: str | None = None

    def extract(self, path: Path) -> ExtractionResult:
        started = time.monotonic()
        file_type = path.suffix.lower().lstrip(".")
        try:
            source_size = path.stat().st_size
        except OSError as exc:
            return ExtractionResult(
                status="error", error=str(exc), file_type=file_type,
                duration_seconds=time.monotonic() - started,
            )
        maximum = self.options.max_file_size_mb * 1024 * 1024
        if source_size > maximum:
            return ExtractionResult(
                status="skipped_large",
                error=f"Datei größer als {self.options.max_file_size_mb} MB",
                file_type=file_type,
                source_size=source_size,
                duration_seconds=time.monotonic() - started,
            )
        try:
            result = self._extract(path, file_type)
            status = result.status
            if status not in {"timeout", "error", "encrypted", "skipped_large"}:
                status = "success" if result.text.strip() else "empty"
            return replace(
                result,
                status=status,
                file_type=file_type,
                source_size=source_size,
                duration_seconds=time.monotonic() - started,
            )
        except TimeoutError as exc:
            return ExtractionResult(
                status="timeout", error=str(exc), parser=file_type,
                file_type=file_type, source_size=source_size,
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            message = str(exc)
            status = "encrypted" if any(
                value in message.casefold() for value in ("encrypted", "password")
            ) else "error"
            return ExtractionResult(
                status=status, error=message, parser=file_type,
                file_type=file_type, source_size=source_size,
                duration_seconds=time.monotonic() - started,
            )

    def _extract(self, path: Path, file_type: str) -> ExtractionResult:
        started = time.monotonic()
        if file_type in self.TEXT_TYPES:
            text = self._limit(path.read_text(encoding="utf-8", errors="replace"))
            return ExtractionResult(text=text, parser="python-text", parser_seconds=time.monotonic() - started)
        if file_type == "pdf":
            return self._pdf_result(path)
        if file_type == "docx":
            text = self._docx(path)
            return ExtractionResult(text=text, parser="python-docx", parser_seconds=time.monotonic() - started)
        if file_type in {"doc", "xls"}:
            text = self._legacy(path, file_type)
            return ExtractionResult(text=text, parser=f"legacy-{file_type}", parser_seconds=time.monotonic() - started)
        if file_type == "xlsx":
            text = self._xlsx(path)
            return ExtractionResult(text=text, parser="openpyxl", parser_seconds=time.monotonic() - started)
        return ExtractionResult(parser="unsupported")

    def _limit(self, text: str) -> str:
        return text[: self.options.max_extracted_characters]

    def _pdf(self, path: Path) -> str:
        """Compatibility helper retained for format-level callers and tests."""
        return self._pdf_result(path).text

    def _pdf_result(self, path: Path) -> ExtractionResult:
        deadline = time.monotonic() + self.options.pdf_text_timeout_seconds
        parser_started = time.monotonic()
        pdf_pages = self._pdf_page_count(path, deadline)
        text = ""
        parser = ""
        pdftotext = self.tools.resolve("pdftotext")
        if pdftotext is not None:
            result = self._run(
                [str(pdftotext), "-enc", "UTF-8", str(path), "-"],
                timeout=self._remaining(deadline),
            )
            if result.returncode == 0:
                text = self._limit(result.stdout)
                parser = "pdftotext"
        if not text.strip():
            result = self._run(
                [
                    sys.executable, "-m", "app.services.pdf_text_extractor",
                    str(path), "--maximum-characters",
                    str(self.options.max_extracted_characters),
                ],
                timeout=self._remaining(deadline),
                cwd=Path(__file__).resolve().parents[2],
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "PyPDF2-Lesefehler")
            text = self._limit(result.stdout)
            parser = "PyPDF2"
        parser_seconds = time.monotonic() - parser_started
        if text.strip() or not self.options.ocr_enabled:
            return ExtractionResult(
                text=text, parser=parser, parser_seconds=parser_seconds,
                page_count=pdf_pages,
            )
        ocr_started = time.monotonic()
        ocr_text, pages, ocr_page_count = self._ocr_pdf(path)
        return ExtractionResult(
            text=ocr_text,
            parser=f"{parser}+ocr" if parser else "ocr",
            parser_seconds=parser_seconds,
            ocr_seconds=time.monotonic() - ocr_started,
            ocr_pages=pages,
            page_count=pdf_pages or ocr_page_count,
        )

    def _pdf_page_count(self, path: Path, deadline: float) -> int | None:
        pdfinfo = self.tools.resolve("pdfinfo")
        if pdfinfo is None:
            return None
        try:
            result = self._run(
                [str(pdfinfo), str(path)], timeout=min(5, self._remaining(deadline))
            )
        except (OSError, TimeoutError):
            return None
        if result.returncode != 0:
            return None
        match = re.search(r"(?mi)^Pages:\s*(\d+)\s*$", result.stdout)
        return int(match.group(1)) if match else None

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PDF-Zeitlimit erreicht")
        return remaining

    def _docx(self, path: Path) -> str:
        document = Document(str(path))
        parts = [item.text for item in document.paragraphs if item.text]
        length = sum(map(len, parts))
        for table in document.tables:
            for row in table.rows:
                values = [cell.text for cell in row.cells if cell.text]
                if values:
                    line = "\t".join(values)
                    parts.append(line)
                    length += len(line)
                if length >= self.options.max_extracted_characters:
                    return self._limit("\n".join(parts))
        return self._limit("\n".join(parts))

    def _xlsx(self, path: Path) -> str:
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        try:
            parts: list[str] = []
            length = 0
            for worksheet in workbook.worksheets:
                parts.append(worksheet.title)
                for row in worksheet.iter_rows(values_only=True):
                    values = [str(value) for value in row if value is not None]
                    if values:
                        line = "\t".join(values)
                        parts.append(line)
                        length += len(line)
                    if length >= self.options.max_extracted_characters:
                        return self._limit("\n".join(parts))
            return self._limit("\n".join(parts))
        finally:
            workbook.close()

    def _legacy(self, path: Path, file_type: str) -> str:
        module = f"app.services.{file_type}_text_extractor"
        timeout = self.LEGACY_XLS_TIMEOUT_SECONDS if file_type == "xls" else self.LEGACY_DOC_TIMEOUT_SECONDS
        try:
            result = self._run(
                [
                    sys.executable, "-m", module, str(path),
                    "--maximum-characters", str(self.options.max_extracted_characters),
                ],
                timeout=timeout,
                cwd=Path(__file__).resolve().parents[2],
            )
        except TimeoutError as exc:
            raise TimeoutError(f"{file_type.upper()}-Zeitlimit von {timeout} Sekunden erreicht") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Unbekannter {file_type.upper()}-Lesefehler")
        return self._limit(result.stdout)

    def _ocr_pdf(self, path: Path) -> tuple[str, int, int | None]:
        pdftoppm = self.tools.resolve("pdftoppm")
        tesseract = self._find_tesseract()
        if pdftoppm is None or tesseract is None:
            return "", 0, None
        deadline = time.monotonic() + self.options.ocr_timeout_seconds
        initial_end = max(1, self.options.ocr_max_pages)
        extended_end = max(initial_end, self.options.ocr_extended_max_pages)
        with TemporaryDirectory(prefix="papagui-ocr-") as directory:
            texts = self._ocr_range(path, Path(directory), 1, initial_end, pdftoppm, tesseract, deadline)
            if len("".join(texts).strip()) < self.options.ocr_extension_threshold:
                texts.extend(self._ocr_range(
                    path, Path(directory), initial_end + 1, extended_end,
                    pdftoppm, tesseract, deadline,
                ))
            return self._limit("\n".join(texts)), len(texts), None

    def _ocr_range(
        self,
        path: Path,
        directory: Path,
        first: int,
        last: int,
        pdftoppm: Path,
        tesseract: Path,
        deadline: float,
    ) -> list[str]:
        if first > last:
            return []
        prefix = directory / f"stage-{first}"
        render = self._run(
            [
                str(pdftoppm), "-jpeg", "-jpegopt", "quality=85", "-r", "120",
                "-f", str(first), "-l", str(last), str(path), str(prefix),
            ],
            timeout=self._remaining(deadline),
        )
        if render.returncode != 0:
            return []
        texts: list[str] = []
        language = self._get_ocr_language(tesseract)
        for image in sorted(directory.glob(f"{prefix.name}-*.jpg")):
            result = self._run(
                [str(tesseract), str(image), "stdout", "-l", language],
                timeout=self._remaining(deadline),
            )
            texts.append(result.stdout if result.returncode == 0 else "")
        return texts

    def _get_ocr_language(self, tesseract: Path) -> str:
        if self._ocr_language is None:
            try:
                result = self._run([str(tesseract), "--list-langs"], timeout=10)
                languages = set(result.stdout.splitlines()[1:])
            except Exception:
                languages = set()
            self._ocr_language = "deu+eng" if {"deu", "eng"}.issubset(languages) else ("deu" if "deu" in languages else "eng")
        return self._ocr_language

    def _find_tesseract(self) -> Path | None:
        resolved = self.tools.resolve("tesseract")
        if resolved is not None:
            return resolved
        if sys.platform == "win32":
            for name in ("PROGRAMFILES", "LOCALAPPDATA"):
                base = os.environ.get(name)
                if base:
                    candidate = Path(base) / "Tesseract-OCR" / "tesseract.exe"
                    if candidate.exists():
                        return candidate
        return None

    @staticmethod
    def _run(
        command: list[str],
        *,
        timeout: float,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        options: dict = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": max(0.1, timeout),
        }
        if cwd is not None:
            options["cwd"] = str(cwd)
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            return subprocess.run(command, **options)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Zeitlimit für {Path(command[0]).name} erreicht") from exc
