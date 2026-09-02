"""Qt widgets for local, read-only document previews."""

from .catalog_browser import CatalogBrowserWidget
from .file import FileViewerWidget
from .image import ImageViewerWidget
from .pdf import PdfViewerWidget
from .spreadsheet import SpreadsheetViewerWidget
from .text import TextViewerWidget

__all__ = [
    "CatalogBrowserWidget",
    "FileViewerWidget",
    "ImageViewerWidget",
    "PdfViewerWidget",
    "SpreadsheetViewerWidget",
    "TextViewerWidget",
]
