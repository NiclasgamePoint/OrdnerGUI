"""Composite file viewer routing local catalog paths to focused widgets."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from papagui_client.viewers.conversion import DocumentPreviewConverter
from papagui_client.viewers.documents import (
    DocxTextReader,
    FilePreviewRouter,
    TextFileReader,
)
from papagui_client.viewers.models import ConversionOutcome, PreviewKind

from .buttons import viewer_button
from .conversion_worker import FileConversionWorker
from .image import ImageViewerWidget
from .pdf import PdfViewerWidget
from .spreadsheet import SpreadsheetViewerWidget
from .text import TextViewerWidget


class FileViewerWidget(QWidget):
    """Preview local copies without importing server or index implementation."""

    def __init__(
        self,
        *,
        converter_factory: Callable[[], DocumentPreviewConverter] | None = None,
        preview_cache_root: Path | None = None,
        router: FilePreviewRouter | None = None,
        text_reader: TextFileReader | None = None,
        docx_reader: DocxTextReader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FileViewer")
        self.current_file: Path | None = None
        self._router = router or FilePreviewRouter()
        self._text_reader = text_reader or TextFileReader()
        self._docx_reader = docx_reader or DocxTextReader()
        self._converter_factory = converter_factory or partial(
            DocumentPreviewConverter, cache_root=preview_cache_root
        )
        self._load_generation = 0
        self._conversion_workers: set[FileConversionWorker] = set()
        self._active_converter: DocumentPreviewConverter | None = None
        self._word_preview_error = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.file_info_label = QLabel("Keine Datei geladen")
        self.file_info_label.setObjectName("ViewerFileInfo")
        header.addWidget(self.file_info_label, 1)
        self.open_folder_button = viewer_button("Ordner")
        self.open_external_button = viewer_button("Extern öffnen")
        self.open_folder_button.clicked.connect(self.open_containing_folder)
        self.open_external_button.clicked.connect(self.open_externally)
        self.open_folder_button.setEnabled(False)
        self.open_external_button.setEnabled(False)
        header.addWidget(self.open_folder_button)
        header.addWidget(self.open_external_button)
        layout.addLayout(header)

        self.stack = QStackedWidget()
        self.stack.setObjectName("ViewerStack")
        self.empty_label = QLabel("Keine Datei geladen")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_widget = QWidget()
        loading_layout = QVBoxLayout(self.loading_widget)
        loading_layout.addStretch()
        self.loading_indicator = QProgressBar()
        self.loading_indicator.setRange(0, 0)
        self.loading_indicator.setTextVisible(False)
        self.loading_indicator.setMaximumWidth(220)
        self.loading_label = QLabel("Datei wird vorbereitet …")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setWordWrap(True)
        loading_layout.addWidget(
            self.loading_indicator, alignment=Qt.AlignmentFlag.AlignCenter
        )
        loading_layout.addWidget(self.loading_label)
        loading_layout.addStretch()
        self.pdf_viewer = PdfViewerWidget()
        self.spreadsheet_viewer = SpreadsheetViewerWidget()
        self.text_viewer = TextViewerWidget()
        self.image_viewer = ImageViewerWidget()
        for widget in (
            self.empty_label,
            self.loading_widget,
            self.pdf_viewer,
            self.spreadsheet_viewer,
            self.text_viewer,
            self.image_viewer,
        ):
            self.stack.addWidget(widget)
        layout.addWidget(self.stack, 1)
        self.conversion_meta_label = QLabel()
        self.conversion_meta_label.setObjectName("ViewerMeta")
        self.conversion_meta_label.setWordWrap(True)
        self.conversion_meta_label.hide()
        layout.addWidget(self.conversion_meta_label)

    @property
    def is_converting(self) -> bool:
        return any(worker.isRunning() for worker in self._conversion_workers)

    def open_file(self, filepath: Path) -> bool:
        selection = self._router.select(filepath)
        if not selection.path.is_file():
            QMessageBox.warning(self, "Fehler", f"Datei nicht gefunden: {selection.path}")
            return False
        self._prepare_for_file(selection.path)
        try:
            if selection.kind is PreviewKind.PDF:
                self._show_pdf(selection.path)
            elif selection.kind is PreviewKind.SPREADSHEET:
                self._show_spreadsheet(selection.path)
            elif selection.kind is PreviewKind.TEXT:
                self._show_text(self._text_reader.read(selection.path))
            elif selection.kind is PreviewKind.WORD:
                self._show_word_preview(selection.path)
            elif selection.kind is PreviewKind.IMAGE:
                self._show_image(selection.path)
            else:
                self._show_text(
                    f"Dateiformat: {selection.path.suffix or 'ohne Endung'}\n\n"
                    "Für dieses Format steht keine interne Vorschau zur Verfügung. "
                    "Die Datei kann über ‚Extern öffnen‘ angezeigt werden."
                )
        except Exception as exc:
            self._show_text(f"Datei konnte nicht angezeigt werden:\n\n{exc}")
        return True

    def show_message(self, message: str) -> None:
        self._show_text(message)

    def _prepare_for_file(self, path: Path) -> None:
        self._load_generation += 1
        self._cancel_conversions()
        self._cleanup_active_converter()
        self._word_preview_error = ""
        self.pdf_viewer.close_document()
        self.current_file = path
        self.file_info_label.setText(f"{path.name} · {path.stat().st_size / 1024:.1f} KB")
        self.open_folder_button.setEnabled(True)
        self.open_external_button.setEnabled(True)
        self._set_conversion_meta(None)

    def _show_pdf(self, path: Path) -> None:
        self.pdf_viewer.load(path)
        self.stack.setCurrentWidget(self.pdf_viewer)
        self._set_conversion_meta(None)

    def _show_spreadsheet(self, path: Path) -> None:
        try:
            self.spreadsheet_viewer.load(path)
        except Exception:
            if path.suffix.lower() != ".xls":
                raise
            self._start_conversion(
                FileConversionWorker.XLS_TO_XLSX,
                path,
                "Excel-Datei wird konvertiert …",
            )
            return
        self.stack.setCurrentWidget(self.spreadsheet_viewer)
        self._set_conversion_meta(None)

    def _show_word_preview(self, path: Path) -> None:
        self._start_conversion(
            FileConversionWorker.WORD_TO_PDF,
            path,
            "Word-Datei wird als formatierte Vorschau vorbereitet …",
        )

    def _start_conversion(self, operation: str, path: Path, message: str) -> None:
        self.loading_label.setText(message)
        self.stack.setCurrentWidget(self.loading_widget)
        self.conversion_meta_label.setText(
            "Die Konvertierung läuft unabhängig von der Oberfläche."
        )
        self.conversion_meta_label.show()
        worker = FileConversionWorker(
            self._load_generation,
            operation,
            path,
            self._converter_factory,
            parent=self,
        )
        worker.completed.connect(self._on_conversion_completed)
        worker.finished.connect(partial(self._release_conversion_worker, worker))
        self._conversion_workers.add(worker)
        worker.start()

    def _on_conversion_completed(
        self,
        generation: int,
        operation: str,
        outcome: ConversionOutcome | None,
        error: str,
        worker: FileConversionWorker,
    ) -> None:
        try:
            if generation != self._load_generation:
                return
            if error:
                if error != "abgebrochen":
                    self._handle_conversion_error(operation, error)
                return
            if outcome is None:
                raise RuntimeError("Konverter lieferte kein Ergebnis")
            if operation == FileConversionWorker.XLS_TO_XLSX:
                if outcome.path is None:
                    raise RuntimeError("Konverter lieferte keine XLSX-Datei")
                self.spreadsheet_viewer.load(outcome.path)
                self._active_converter = worker.take_converter()
                self.stack.setCurrentWidget(self.spreadsheet_viewer)
            elif operation == FileConversionWorker.WORD_TO_PDF:
                if outcome.path is None:
                    raise RuntimeError("Konverter lieferte keine PDF-Datei")
                self.pdf_viewer.load(outcome.path)
                self._active_converter = worker.take_converter()
                self.stack.setCurrentWidget(self.pdf_viewer)
            elif operation == FileConversionWorker.EXTRACT_DOC:
                if not outcome.text or not outcome.text.strip():
                    raise RuntimeError("Inhalt der DOC-Datei konnte nicht extrahiert werden")
                text = outcome.text
                if self._word_preview_error:
                    text = (
                        "Formatierte Word-Vorschau konnte nicht erstellt werden:\n"
                        f"{self._word_preview_error}\n\nTextinhalt:\n\n{text}"
                    )
                    self._word_preview_error = ""
                self._show_text(text)
            else:
                raise ValueError(f"Unbekannte Konvertierung: {operation}")
            self._set_conversion_meta(outcome)
        except Exception as exc:
            if generation == self._load_generation:
                self._set_conversion_meta(None)
                self._show_text(f"Datei konnte nicht angezeigt werden:\n\n{exc}")

    def _handle_conversion_error(self, operation: str, error: str) -> None:
        if operation == FileConversionWorker.WORD_TO_PDF and self.current_file is not None:
            self._word_preview_error = error
            if self.current_file.suffix.lower() == ".docx":
                try:
                    text = self._docx_reader.read(self.current_file)
                    self._show_text(
                        "Formatierte Word-Vorschau konnte nicht erstellt werden:\n"
                        f"{error}\n\nTextinhalt:\n\n{text}"
                    )
                    self._word_preview_error = ""
                    self._set_conversion_meta(None)
                    return
                except Exception as fallback_error:
                    self._show_text(
                        f"Datei konnte nicht angezeigt werden:\n\n{fallback_error}"
                    )
                    return
            if self.current_file.suffix.lower() == ".doc":
                self._start_conversion(
                    FileConversionWorker.EXTRACT_DOC,
                    self.current_file,
                    "Word-Datei wird als Textvorschau verarbeitet …",
                )
                return
        self._set_conversion_meta(None)
        self._show_text(f"Datei konnte nicht konvertiert werden:\n\n{error}")

    def _show_text(self, text: str) -> None:
        self.text_viewer.set_text(text[:2_000_000])
        self.stack.setCurrentWidget(self.text_viewer)

    def _show_image(self, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise ValueError("Bild konnte nicht geladen werden")
        self.image_viewer.set_image(pixmap)
        self.stack.setCurrentWidget(self.image_viewer)
        self._set_conversion_meta(None)

    def _set_conversion_meta(self, outcome: ConversionOutcome | None) -> None:
        if outcome is not None and outcome.converted:
            self.conversion_meta_label.setText(f"Konvertiert mit: {outcome.tool}")
            self.conversion_meta_label.show()
        else:
            self.conversion_meta_label.clear()
            self.conversion_meta_label.hide()

    def open_externally(self) -> None:
        self._open_url(self.current_file, "Datei")

    def open_containing_folder(self) -> None:
        self._open_url(
            self.current_file.parent if self.current_file is not None else None,
            "Ordner",
        )

    def _open_url(self, target: Path | None, label: str) -> None:
        if target is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            QMessageBox.warning(
                self,
                f"{label} konnte nicht geöffnet werden",
                f"Kein Standardprogramm verfügbar oder Start fehlgeschlagen:\n{target}",
            )

    def _release_conversion_worker(self, worker: FileConversionWorker) -> None:
        self._conversion_workers.discard(worker)
        worker.cleanup()
        worker.deleteLater()

    def _cancel_conversions(self) -> None:
        for worker in tuple(self._conversion_workers):
            if worker.isRunning():
                worker.requestInterruption()

    def _cleanup_active_converter(self) -> None:
        if self._active_converter is not None:
            self._active_converter.cleanup()
            self._active_converter = None

    def shutdown(self) -> None:
        self._load_generation += 1
        self._cancel_conversions()
        for worker in tuple(self._conversion_workers):
            worker.wait()
            worker.cleanup()
        self._conversion_workers.clear()
        self._cleanup_active_converter()
        self.pdf_viewer.close_document()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
