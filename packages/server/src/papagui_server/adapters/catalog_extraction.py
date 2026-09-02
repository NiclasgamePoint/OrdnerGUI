"""Bounded document extraction adapters for the headless indexer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
import xml.etree.ElementTree as ElementTree
import zipfile

from papagui_server.domain.models import ServerSettings


TEXT_EXTENSIONS = {
    "txt", "csv", "md", "log", "json", "xml", "yaml", "yml", "ini"
}


@dataclass(frozen=True, slots=True)
class ExtractionResourcePolicy:
    pdf_timeout_seconds: int
    ocr_timeout_seconds: int
    ocr_pages: int
    render_dpi: int
    pause_seconds: float

    @classmethod
    def for_document(
        cls, path: Path, settings: ServerSettings
    ) -> "ExtractionResourcePolicy":
        preferred = {
            value.strip().casefold()
            for value in settings.preferred_document_patterns.split(",")
            if value.strip()
        }
        extended = any(value in path.stem.casefold() for value in preferred)
        pages = settings.ocr_extended_max_pages if extended else settings.ocr_max_pages
        profile = {
            "gentle": (150, 0.05),
            "balanced": (200, 0.01),
            "fast": (250, 0.0),
        }[settings.resource_profile]
        return cls(
            pdf_timeout_seconds=settings.pdf_text_timeout_seconds,
            ocr_timeout_seconds=settings.ocr_timeout_seconds,
            ocr_pages=pages,
            render_dpi=profile[0],
            pause_seconds=profile[1],
        )


class ExternalCommandRunner:
    """Run allow-listed argv commands without a shell and with a hard timeout."""

    def run(self, command: list[str], *, timeout: int) -> bytes:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return b""
        return result.stdout if result.returncode == 0 else b""


class DocumentTextExtractor:
    """Best-effort office/PDF extraction with bounded optional Tesseract OCR."""

    def __init__(self, runner: ExternalCommandRunner | None = None) -> None:
        self._runner = runner or ExternalCommandRunner()

    def extract(
        self,
        path: Path,
        settings: ServerSettings,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> str:
        extension = path.suffix.casefold().lstrip(".")
        maximum = settings.max_extracted_characters
        if extension in TEXT_EXTENSIONS:
            try:
                return path.read_text(encoding="utf-8", errors="replace")[:maximum]
            except OSError:
                return ""
        if extension == "pdf":
            return self._pdf(path, settings, cancelled)
        if extension == "doc":
            return self._program(["antiword", str(path)], maximum, timeout=45)
        if extension == "xls":
            return self._program(["catdoc", str(path)], maximum, timeout=45)
        if extension in {"docx", "xlsx"}:
            return self._office_xml(path, maximum)
        return ""

    def _program(self, command: list[str], maximum: int, *, timeout: int) -> str:
        return self._runner.run(command, timeout=timeout).decode(
            "utf-8", errors="replace"
        )[:maximum]

    def _pdf(
        self,
        path: Path,
        settings: ServerSettings,
        cancelled: Callable[[], bool],
    ) -> str:
        policy = ExtractionResourcePolicy.for_document(path, settings)
        text = self._program(
            ["pdftotext", "-layout", str(path), "-"],
            settings.max_extracted_characters,
            timeout=policy.pdf_timeout_seconds,
        )
        if (
            not settings.ocr_enabled
            or len(text.strip()) >= settings.ocr_extension_threshold
            or cancelled()
        ):
            return text
        with TemporaryDirectory(prefix="papagui-ocr-") as temporary:
            output_prefix = Path(temporary) / "page"
            self._runner.run(
                [
                    "pdftoppm", "-png", "-f", "1", "-l", str(policy.ocr_pages),
                    "-r", str(policy.render_dpi), str(path), str(output_prefix),
                ],
                timeout=policy.pdf_timeout_seconds,
            )
            parts = [text] if text else []
            for image in sorted(Path(temporary).glob("page-*.png"))[: policy.ocr_pages]:
                if cancelled():
                    break
                value = self._program(
                    ["tesseract", str(image), "stdout", "-l", "deu"],
                    settings.max_extracted_characters,
                    timeout=policy.ocr_timeout_seconds,
                ).strip()
                if value:
                    parts.append(value)
                if sum(len(part) for part in parts) >= settings.max_extracted_characters:
                    break
                if policy.pause_seconds:
                    time.sleep(policy.pause_seconds)
            return "\n".join(parts)[: settings.max_extracted_characters]

    @staticmethod
    def _office_xml(path: Path, maximum: int) -> str:
        try:
            with zipfile.ZipFile(path) as bundle:
                names = (
                    ["word/document.xml"]
                    if path.suffix.casefold() == ".docx"
                    else [
                        name for name in bundle.namelist()
                        if name == "xl/sharedStrings.xml"
                        or name.startswith("xl/worksheets/")
                    ]
                )
                values: list[str] = []
                length = 0
                for name in names:
                    try:
                        root = ElementTree.fromstring(bundle.read(name))
                    except (KeyError, ElementTree.ParseError):
                        continue
                    for node in root.iter():
                        if node.text and node.text.strip():
                            values.append(node.text.strip())
                            length += len(values[-1]) + 1
                            if length >= maximum:
                                return "\n".join(values)[:maximum]
                return "\n".join(values)[:maximum]
        except (OSError, zipfile.BadZipFile):
            return ""
