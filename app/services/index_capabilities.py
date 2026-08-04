from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import sys

from app.services.index_tool_resolver import IndexToolResolver


@dataclass(frozen=True)
class IndexCapabilities:
    tesseract_path: str = ""
    poppler_path: str = ""
    pdftotext_path: str = ""
    ocr_languages: tuple[str, ...] = ()

    @property
    def ocr_available(self) -> bool:
        return bool(self.tesseract_path and self.poppler_path)


class IndexCapabilityDetector:
    """Detect optional extraction tools without platform-specific assumptions."""

    def detect(self) -> IndexCapabilities:
        resolver = IndexToolResolver()
        tesseract = self._find_tesseract()
        poppler_path = resolver.resolve("pdftoppm")
        pdftotext_path = resolver.resolve("pdftotext")
        poppler = str(poppler_path) if poppler_path else ""
        pdftotext = str(pdftotext_path) if pdftotext_path else ""
        languages: tuple[str, ...] = ()
        if tesseract:
            languages = self._tesseract_languages(Path(tesseract))
        return IndexCapabilities(tesseract, poppler, pdftotext, languages)

    @staticmethod
    def _find_tesseract() -> str:
        found = shutil.which("tesseract")
        if found:
            return found
        if sys.platform == "win32":
            for variable in ("PROGRAMFILES", "LOCALAPPDATA"):
                base = os.environ.get(variable)
                if not base:
                    continue
                candidate = Path(base) / "Tesseract-OCR" / "tesseract.exe"
                if candidate.exists():
                    return str(candidate)
        return ""

    @staticmethod
    def _tesseract_languages(executable: Path) -> tuple[str, ...]:
        try:
            result = subprocess.run(
                [str(executable), "--list-langs"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        if result.returncode != 0:
            return ()
        return tuple(
            sorted(line.strip() for line in result.stdout.splitlines()[1:] if line.strip())
        )
