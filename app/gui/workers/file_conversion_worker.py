from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.services.document_converter import DocumentConverter


class FileConversionWorker(QThread):
    """Run blocking legacy document conversion outside the GUI thread."""

    completed = Signal(int, str, object, object, str)

    XLS_TO_XLSX = "xls_to_xlsx"
    EXTRACT_DOC = "extract_doc"
    WORD_TO_PDF = "word_to_pdf"

    def __init__(
        self,
        generation: int,
        operation: str,
        source_path: Path,
        parent=None,
    ):
        super().__init__(parent)
        self.generation = generation
        self.operation = operation
        self.source_path = source_path
        self.converter: DocumentConverter | None = None

    def run(self):
        self.converter = DocumentConverter()
        try:
            if self.operation == self.XLS_TO_XLSX:
                result = self.converter.convert(
                    self.source_path,
                    "xlsx",
                    should_cancel=self.isInterruptionRequested,
                )
            elif self.operation == self.EXTRACT_DOC:
                result = self.converter.extract_legacy_doc(
                    self.source_path,
                    should_cancel=self.isInterruptionRequested,
                )
            elif self.operation == self.WORD_TO_PDF:
                result = self.converter.convert_word_to_pdf(
                    self.source_path,
                    should_cancel=self.isInterruptionRequested,
                )
            else:
                raise ValueError(f"Unbekannte Konvertierung: {self.operation}")

            if self.isInterruptionRequested():
                raise InterruptedError("Konvertierung wurde abgebrochen")
            self.completed.emit(
                self.generation,
                self.operation,
                result,
                self.converter.get_last_operation(),
                "",
            )
        except InterruptedError:
            self.completed.emit(
                self.generation,
                self.operation,
                None,
                {},
                "abgebrochen",
            )
        except Exception as exc:
            self.completed.emit(
                self.generation,
                self.operation,
                None,
                self.converter.get_last_operation(),
                str(exc),
            )

    def cleanup(self):
        if self.converter is not None:
            self.converter.cleanup()

    def take_converter(self) -> DocumentConverter | None:
        converter = self.converter
        self.converter = None
        return converter
