from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.core.index_layout import IndexLayout
from app.services.content_index_maintenance import ContentIndexMaintenance


class ContentMaintenanceWorker(QThread):
    """Run potentially expensive content-index maintenance off the GUI thread."""

    completed = Signal(str, object, str)

    def __init__(self, layout: IndexLayout, action: str, parent=None):
        super().__init__(parent)
        self.layout = layout
        self.action = action

    def run(self):
        try:
            service = ContentIndexMaintenance(self.layout)
            operation = getattr(service, self.action)
            result = operation()
            self.completed.emit(self.action, result, "")
        except Exception as exc:
            self.completed.emit(self.action, None, str(exc))
