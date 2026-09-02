"""Qt implementation of the presentation timer port."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer


class QtTimerAdapter:
    def __init__(self, parent: QObject):
        self._timer = QTimer(parent)
        self._callback: Callable[[], None] | None = None
        self._timer.timeout.connect(self._fire)

    @property
    def interval_seconds(self) -> float:
        return self._timer.interval() / 1000

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self, interval_seconds: int, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._timer.start(interval_seconds * 1000)

    def stop(self) -> None:
        self._timer.stop()
        self._callback = None

    def _fire(self) -> None:
        if self._callback is not None:
            self._callback()
