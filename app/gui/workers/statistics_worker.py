from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.core.statistics import StatisticsService


class StatisticsWorker(QThread):
    completed = Signal(object, str)

    def __init__(
        self,
        index_path: Path,
        customer_database_path: Path,
        content_state_path: Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.index_path = index_path
        self.customer_database_path = customer_database_path
        self.content_state_path = content_state_path

    def run(self):
        try:
            statistics = StatisticsService().load(
                self.index_path,
                self.customer_database_path,
                self.content_state_path,
            )
            self.completed.emit(statistics, "")
        except Exception as error:
            self.completed.emit(None, str(error))
