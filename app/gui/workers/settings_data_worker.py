from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.core.customer_repository import CustomerRepository
from app.core.catalog_index import CatalogStore
from app.core.index_diagnostics import IndexDiagnosticsService
from app.core.index_layout import IndexLayout
from app.core.index_store import available_backups
from app.core.statistics import StatisticsService


class SettingsDataWorker(QThread):
    """Load slower settings data without delaying popup creation."""

    completed = Signal(object)

    def __init__(
        self,
        index_path: Path,
        customer_database_path: Path,
        index_layout: IndexLayout | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.index_path = index_path
        self.customer_database_path = customer_database_path
        self.index_layout = index_layout

    def run(self):
        payload = {
            "backups": [],
            "diagnostics": None,
            "recognition_summary": {},
            "pending_recognition_cases": 0,
            "blacklist_suggestions": [],
            "statistics": None,
            "error": "",
        }
        repository = None
        try:
            payload["backups"] = (
                CatalogStore(self.index_layout).available_backups()
                if self.index_layout is not None
                else available_backups(self.index_path)
            )
            state_path = (
                self.index_layout.content_state_path
                if self.index_layout is not None else None
            )
            payload["diagnostics"] = IndexDiagnosticsService().inspect(
                self.index_path, state_path
            )
            payload["statistics"] = StatisticsService().load(
                self.index_path,
                self.customer_database_path,
                state_path,
            )
            repository = CustomerRepository(self.customer_database_path)
            payload["recognition_summary"] = repository.last_recognition_run()
            payload["pending_recognition_cases"] = repository.pending_recognition_count()
            payload["blacklist_suggestions"] = repository.list_blacklist_suggestions()
        except Exception as error:
            payload["error"] = str(error)
        finally:
            if repository is not None:
                repository.close()
        self.completed.emit(payload)


class BlacklistCleanupWorker(QThread):
    """Remove only provenance-backed values without blocking the UI."""

    completed = Signal(object, str)

    def __init__(self, customer_database_path: Path, options, parent=None):
        super().__init__(parent)
        self.customer_database_path = customer_database_path
        self.options = options

    def run(self):
        repository = None
        try:
            repository = CustomerRepository(self.customer_database_path)
            result = repository.cleanup_automatic_blacklisted_values(self.options)
            self.completed.emit(result, "")
        except Exception as error:
            self.completed.emit({}, str(error))
        finally:
            if repository is not None:
                repository.close()
