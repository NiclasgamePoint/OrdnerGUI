from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.core.config import CustomerRecognitionOptions
from app.services.customer_recognition import CustomerRecognitionService


class ContactScanWorker(QThread):
    """Run one customer's indexed contact scan without blocking the UI."""

    completed = Signal(object, str)

    def __init__(
        self,
        index_path: Path,
        customer_database_path: Path,
        options: CustomerRecognitionOptions,
        customer_id: int,
        parent=None,
    ):
        super().__init__(parent)
        self.index_path = index_path
        self.customer_database_path = customer_database_path
        self.options = options
        self.customer_id = customer_id

    def run(self):
        try:
            result = CustomerRecognitionService(
                self.index_path,
                self.customer_database_path,
                self.options,
            ).rescan_customer_contacts(self.customer_id)
            self.completed.emit(result, "")
        except Exception as error:
            self.completed.emit(None, str(error))
