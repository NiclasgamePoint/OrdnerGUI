from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.services.index_distribution import IndexGenerationClient, IndexSyncError


class IndexSyncWorker(QThread):
    """Download one remote generation without blocking the GUI event loop."""

    synchronized = Signal(bool)
    failed = Signal(str)

    def __init__(self, client: IndexGenerationClient, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            self.synchronized.emit(self.client.sync())
        except IndexSyncError as exc:
            self.failed.emit(str(exc))
