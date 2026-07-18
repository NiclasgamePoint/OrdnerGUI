from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.core.customer_repository import CustomerRepository
from app.core.index_diagnostics import IndexDiagnosticsService
from app.core.index_store import available_backups


class SettingsDataWorker(QThread):
    """Load slower settings data without delaying popup creation."""

    completed = Signal(object)

    def __init__(self, index_path: Path, customer_database_path: Path, parent=None):
        super().__init__(parent)
        self.index_path = index_path
        self.customer_database_path = customer_database_path

    def run(self):
        payload = {
            "backups": [],
            "diagnostics": None,
            "recognition_summary": {},
            "pending_recognition_cases": 0,
            "error": "",
        }
        repository = None
        try:
            payload["backups"] = available_backups(self.index_path)
            payload["diagnostics"] = IndexDiagnosticsService().inspect(self.index_path)
            repository = CustomerRepository(self.customer_database_path)
            payload["recognition_summary"] = repository.last_recognition_run()
            payload["pending_recognition_cases"] = repository.pending_recognition_count()
        except Exception as error:
            payload["error"] = str(error)
        finally:
            if repository is not None:
                repository.close()
        self.completed.emit(payload)
