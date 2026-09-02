"""Small Qt thread-pool adapter used by both client windows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class BackgroundTask(QRunnable):
    def __init__(self, operation: Callable[[], Any]):
        super().__init__()
        self._operation = operation
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation()
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
