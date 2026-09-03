from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout

from papagui_client.gui.widgets.buttons import AppButton


class ResultRow(QFrame):
    """Reusable clickable row with a separate native-folder action."""

    activated = Signal(object)
    openPathRequested = Signal(str)

    def __init__(
        self,
        title: str,
        subtitle: str,
        payload: Any,
        path: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("ResultRow")
        self.setProperty("clickable", True)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMinimumHeight(62)
        self._payload = payload
        self._path = path
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle or "Suchergebnis")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 10, 9)
        layout.setSpacing(12)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("ResultTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("ResultSubtitle")
        subtitle_label.setWordWrap(True)
        text_layout.addWidget(title_label)
        if subtitle:
            text_layout.addWidget(subtitle_label)
        layout.addLayout(text_layout, 1)

        self.open_button = AppButton(
            "Öffnen",
            AppButton.SECONDARY,
            minimum_width=102,
        )
        self.open_button.setToolTip("Ordner im Explorer oder Finder öffnen")
        self.open_button.setAccessibleName(f"Ordner zu {title} öffnen")
        self.open_button.setEnabled(bool(path))
        self.open_button.clicked.connect(self._open_path)
        layout.addWidget(self.open_button)

    @property
    def payload(self) -> Any:
        return self._payload

    @property
    def path(self) -> str:
        return self._path

    def _open_path(self):
        if self._path:
            self.openPathRequested.emit(self._path)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit(self._payload)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        open_action = menu.addAction("Oeffnen/Anzeigen")
        open_path_action = menu.addAction("Ordner im System oeffnen")
        copy_action = menu.addAction("Pfad kopieren")
        open_path_action.setEnabled(bool(self._path))
        copy_action.setEnabled(bool(self._path))

        selected = menu.exec(event.globalPos())
        if selected == open_action:
            self.activated.emit(self._payload)
            return
        if selected == open_path_action and self._path:
            self.openPathRequested.emit(self._path)
            return
        if selected == copy_action and self._path:
            QApplication.clipboard().setText(self._path)

