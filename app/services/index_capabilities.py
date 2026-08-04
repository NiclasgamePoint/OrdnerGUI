from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import sys


@dataclass(frozen=True)
class IndexCapabilities:
    tesseract_path: str = ""
    poppler_path: str = ""
    ocr_languages: tuple[str, ...] = ()

    @property
    def ocr_available(self) -> bool:
        return bool(self.tesseract_path and self.poppler_path)


class IndexCapabilityDetector:
    """Detect optional extraction tools without platform-specific assumptions."""

    def detect(self) -> IndexCapabilities:
        tesseract = self._find_tesseract()
        poppler = shutil.which("pdftoppm") or ""
        languages: tuple[str, ...] = ()
        if tesseract:
            languages = self._tesseract_languages(Path(tesseract))
        return IndexCapabilities(tesseract, poppler, languages)

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
