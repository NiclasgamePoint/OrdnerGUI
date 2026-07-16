from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal, QStringListModel
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QToolButton,
)

from app.gui.widgets.buttons import AppButton


class AppHeader(QFrame):
    """Persistent application header shared by every page."""

    queryChanged = Signal(str)
    searchRequested = Signal()
    filterRequested = Signal()
    settingsRequested = Signal()

    def __init__(self, history: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("AppHeader")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("GlobalSearchInput")
        self.search_input.setPlaceholderText("Kunden oder Ordner durchsuchen …")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.queryChanged.emit)
        self.search_input.returnPressed.connect(self.searchRequested.emit)

        self.history_model = QStringListModel(history or [], self)
        completer = QCompleter(self.history_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.search_input.setCompleter(completer)

        self.search_button = AppButton("Suchen", minimum_width=108)
        self.search_button.clicked.connect(self.searchRequested.emit)

        self.filter_button = AppButton("Filter", AppButton.SECONDARY, minimum_width=92)
        self.filter_button.clicked.connect(self.filterRequested.emit)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("SettingsButton")
        self.settings_button.setFixedSize(40, 40)
        self.settings_button.setIconSize(QSize(20, 20))
        icon = QIcon.fromTheme("preferences-system")
        if icon.isNull():
            self.settings_button.setText("⚙")
        else:
            self.settings_button.setIcon(icon)
        self.settings_button.setToolTip("Einstellungen öffnen")
        self.settings_button.clicked.connect(self.settingsRequested.emit)

        layout.addWidget(self.search_input, 1)
        layout.addWidget(self.search_button)
        layout.addWidget(self.filter_button)
        layout.addWidget(self.settings_button)

    def query(self) -> str:
        return self.search_input.text().strip()

    def set_query(self, query: str):
        self.search_input.setText(query)

    def set_history(self, entries: list[str]):
        self.history_model.setStringList(entries)

    def set_filter_count(self, count: int):
        self.filter_button.setText("Filter" if count == 0 else f"Filter ({count})")
        self.filter_button.setProperty("filtersActive", count > 0)
        self.filter_button.style().unpolish(self.filter_button)
        self.filter_button.style().polish(self.filter_button)
