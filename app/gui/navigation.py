from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class NavigationEntry:
    page: str
    payload: Any = None


class NavigationController(QObject):
    """Small history-aware router for the application's stacked pages."""

    routeChanged = Signal(object)
    canGoBackChanged = Signal(bool)

    def __init__(self, initial_page: str = "search", parent=None):
        super().__init__(parent)
        self._current = NavigationEntry(initial_page)
        self._history: list[NavigationEntry] = []

    @property
    def current(self) -> NavigationEntry:
        return self._current

    @property
    def can_go_back(self) -> bool:
        return bool(self._history)

    def navigate(self, page: str, payload: Any = None, remember: bool = True):
        destination = NavigationEntry(page, payload)
        if destination == self._current:
            self.routeChanged.emit(destination)
            return
        if remember:
            self._history.append(self._current)
        self._current = destination
        self.routeChanged.emit(destination)
        self.canGoBackChanged.emit(self.can_go_back)

    def back(self):
        if not self._history:
            return
        self._current = self._history.pop()
        self.routeChanged.emit(self._current)
        self.canGoBackChanged.emit(self.can_go_back)

    def reset(self, page: str = "search", payload: Any = None):
        self._history.clear()
        self._current = NavigationEntry(page, payload)
        self.routeChanged.emit(self._current)
        self.canGoBackChanged.emit(False)
