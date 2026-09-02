"""Background bridge between blocking converters and Qt views."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from papagui_client.viewers.conversion import DocumentPreviewConverter


class FileConversionWorker(QThread):
    completed = Signal(int, str, object, str, object)

    XLS_TO_XLSX = "xls_to_xlsx"
    EXTRACT_DOC = "extract_doc"
    WORD_TO_PDF = "word_to_pdf"

    def __init__(
        self,
        generation: int,
        operation: str,
        source_path: Path,
        converter_factory: Callable[[], DocumentPreviewConverter],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.generation = generation
        self.operation = operation
        self.source_path = source_path
        self._converter_factory = converter_factory
        self.converter: DocumentPreviewConverter | None = None

    def run(self) -> None:
        self.converter = self._converter_factory()
        try:
            if self.operation == self.XLS_TO_XLSX:
                outcome = self.converter.convert(
                    self.source_path,
                    "xlsx",
                    should_cancel=self.isInterruptionRequested,
                )
            elif self.operation == self.EXTRACT_DOC:
                outcome = self.converter.extract_legacy_doc(
                    self.source_path,
                    should_cancel=self.isInterruptionRequested,
                )
            elif self.operation == self.WORD_TO_PDF:
                outcome = self.converter.convert_word_to_pdf(
                    self.source_path,
                    should_cancel=self.isInterruptionRequested,
                )
            else:
                raise ValueError(f"Unbekannte Konvertierung: {self.operation}")
            if self.isInterruptionRequested():
                raise InterruptedError("Konvertierung wurde abgebrochen")
            self.completed.emit(
                self.generation, self.operation, outcome, "", self
            )
        except InterruptedError:
            self.completed.emit(
                self.generation, self.operation, None, "abgebrochen", self
            )
        except Exception as exc:
            self.completed.emit(
                self.generation, self.operation, None, str(exc), self
            )

    def take_converter(self) -> DocumentPreviewConverter | None:
        converter = self.converter
        self.converter = None
        return converter

    def cleanup(self) -> None:
        if self.converter is not None:
            self.converter.cleanup()
            self.converter = None
