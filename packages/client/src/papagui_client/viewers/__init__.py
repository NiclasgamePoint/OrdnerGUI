"""Read-only document preview services.

The module deliberately has no Qt dependency.  GUI widgets live below
``papagui_client.gui.viewers`` and consume these services through small ports.
"""

from .conversion import DocumentPreviewConverter
from .documents import DocxTextReader, FilePreviewRouter, TextFileReader
from .models import (
    ConversionOutcome,
    PreviewKind,
    PreviewSelection,
    SheetPreview,
)
from .spreadsheets import SpreadsheetPreviewReader
from .tools import DocumentToolResolver

__all__ = [
    "ConversionOutcome",
    "DocumentPreviewConverter",
    "DocumentToolResolver",
    "DocxTextReader",
    "FilePreviewRouter",
    "PreviewKind",
    "PreviewSelection",
    "SheetPreview",
    "SpreadsheetPreviewReader",
    "TextFileReader",
]
