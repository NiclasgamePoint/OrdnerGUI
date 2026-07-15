from __future__ import annotations

from pathlib import Path

from docx import Document
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.gui.viewers import PdfViewerWidget, SpreadsheetViewerWidget, TextViewerWidget
from app.gui.widgets.buttons import AppButton
from app.services.document_converter import DocumentConverter


class FileViewer(QWidget):
    """Route supported formats to focused viewer widgets."""

    TEXT_TYPES = {"txt", "csv", "log", "md", "json", "xml", "yaml", "yml", "ini"}
    IMAGE_TYPES = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FileViewer")
        self.current_file: Path | None = None
        self.converter = DocumentConverter()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.file_info_label = QLabel("Keine Datei geladen")
        self.file_info_label.setObjectName("ViewerFileInfo")
        header.addWidget(self.file_info_label, 1)
        self.open_folder_button = AppButton("Ordner", AppButton.SECONDARY)
        self.open_external_button = AppButton("Extern öffnen", AppButton.SECONDARY)
        self.open_folder_button.clicked.connect(self.open_containing_folder)
        self.open_external_button.clicked.connect(self.open_externally)
        self.open_folder_button.setEnabled(False)
        self.open_external_button.setEnabled(False)
        header.addWidget(self.open_folder_button)
        header.addWidget(self.open_external_button)
        layout.addLayout(header)

        self.stack = QStackedWidget()
        self.empty_label = QLabel("Keine Datei geladen")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.pdf_viewer = PdfViewerWidget()
        self.spreadsheet_viewer = SpreadsheetViewerWidget()
        self.text_viewer = TextViewerWidget()
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_scroll.setWidget(self.image_label)
        for widget in (
            self.empty_label,
            self.pdf_viewer,
            self.spreadsheet_viewer,
            self.text_viewer,
            self.image_scroll,
        ):
            self.stack.addWidget(widget)
        layout.addWidget(self.stack, 1)

    def open_file(self, filepath: Path):
        filepath = filepath.resolve()
        if not filepath.exists() or not filepath.is_file():
            QMessageBox.warning(self, "Fehler", f"Datei nicht gefunden: {filepath}")
            return
        self.pdf_viewer.close_document()
        self.converter.cleanup()
        self.current_file = filepath
        self.file_info_label.setText(
            f"{filepath.name} · {filepath.stat().st_size / 1024:.1f} KB"
        )
        self.open_folder_button.setEnabled(True)
        self.open_external_button.setEnabled(True)
        suffix = filepath.suffix.lower().lstrip(".")
        try:
            if suffix == "pdf":
                self._show_pdf(filepath)
            elif suffix in {"xlsx", "xls"}:
                self._show_spreadsheet(filepath)
            elif suffix in self.TEXT_TYPES:
                self._show_text(filepath.read_text(encoding="utf-8", errors="replace"))
            elif suffix == "docx":
                self._show_docx(filepath)
            elif suffix == "doc":
                self._show_legacy_doc(filepath)
            elif suffix in self.IMAGE_TYPES:
                self._show_image(filepath)
            else:
                self._show_text(
                    f"Dateiformat: {filepath.suffix or 'ohne Endung'}\n\n"
                    "Für dieses Format steht keine interne Vorschau zur Verfügung. "
                    "Die Datei kann über ‚Extern öffnen‘ angezeigt werden."
                )
        except Exception as exc:
            self._show_text(f"Datei konnte nicht angezeigt werden:\n\n{exc}")

    def _show_pdf(self, path: Path):
        self.pdf_viewer.load(path)
        self.stack.setCurrentWidget(self.pdf_viewer)

    def _show_spreadsheet(self, path: Path):
        try:
            self.spreadsheet_viewer.load(path)
        except Exception:
            if path.suffix.lower() != ".xls":
                raise
            converted = self.converter.convert(path, "xlsx")
            self.spreadsheet_viewer.load(converted)
        self.stack.setCurrentWidget(self.spreadsheet_viewer)

    def _show_docx(self, path: Path):
        document = Document(str(path))
        lines = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
        for table in document.tables:
            lines.extend("\t".join(cell.text for cell in row.cells) for row in table.rows)
        self._show_text("\n".join(lines))

    def _show_legacy_doc(self, path: Path):
        try:
            self._show_text(self.converter.extract_legacy_doc(path))
        except Exception:
            self._show_pdf(self.converter.convert(path, "pdf"))

    def _show_text(self, text: str):
        self.text_viewer.set_text(text[:2_000_000])
        self.stack.setCurrentWidget(self.text_viewer)

    def _show_image(self, path: Path):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise ValueError("Bild konnte nicht geladen werden")
        self.image_label.setPixmap(pixmap)
        self.image_label.resize(pixmap.size())
        self.stack.setCurrentWidget(self.image_scroll)

    def open_externally(self):
        if self.current_file is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_file)))

    def open_containing_folder(self):
        if self.current_file is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_file.parent)))

    def closeEvent(self, event):
        self.pdf_viewer.close_document()
        self.converter.cleanup()
        super().closeEvent(event)
