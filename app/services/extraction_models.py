from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractionResult:
    """Content plus format-independent diagnostics for one source document."""

    text: str = ""
    status: str = "empty"
    error: str = ""
    parser: str = ""
    file_type: str = ""
    source_size: int = 0
    page_count: int | None = None
    duration_seconds: float = 0.0
    parser_seconds: float = 0.0
    ocr_seconds: float = 0.0
    ocr_pages: int = 0

    def __iter__(self):
        """Keep legacy tuple-unpacking callers source compatible."""
        yield self.text
        yield self.status
        yield self.error

