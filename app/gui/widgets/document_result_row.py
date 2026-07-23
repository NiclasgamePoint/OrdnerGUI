from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from app.gui.widgets.buttons import AppButton


class DocumentResultRow(QFrame):
    """Search result dedicated to an extracted document-content match."""

    openFileRequested = Signal(str)
    openPathRequested = Signal(str)

    def __init__(self, filename: str, snippet: str, path: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DocumentResultRow")
        self.setProperty("clickable", True)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMinimumHeight(74)
        self._path = path
        self.setAccessibleName(filename)
        self.setAccessibleDescription(
            f"Dokumenttreffer. Textausschnitt: {snippet or 'nicht verfügbar'}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 10, 9)
        layout.setSpacing(12)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        self.title_label = QLabel(filename)
        self.title_label.setObjectName("ResultTitle")
        self.snippet_label = QLabel(snippet or "Kein Textausschnitt verfügbar")
        self.snippet_label.setObjectName("ResultSubtitle")
        self.snippet_label.setWordWrap(True)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.snippet_label)
        layout.addLayout(text_layout, 1)

        self.folder_button = AppButton(
            "Ordner",
            AppButton.SECONDARY,
            minimum_width=84,
        )
        self.folder_button.setToolTip("Enthaltenden Ordner öffnen")
        self.folder_button.setAccessibleName(
            f"Enthaltenden Ordner von {filename} öffnen"
        )
        self.folder_button.clicked.connect(self._open_folder)
        self.open_button = AppButton("Öffnen", minimum_width=92)
        self.open_button.setToolTip("Datei im Standardprogramm öffnen")
        self.open_button.setAccessibleName(f"{filename} extern öffnen")
        self.open_button.clicked.connect(self._open_file)
        layout.addWidget(self.folder_button)
        layout.addWidget(self.open_button)

    @property
    def path(self) -> str:
        return self._path

    def _open_file(self):
        if self._path:
            self.openFileRequested.emit(self._path)

    def _open_folder(self):
        if self._path:
            self.openPathRequested.emit(str(Path(self._path).parent))

    def mouseReleaseEvent(self, event: QMouseEvent):
        if (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self._open_file()
            event.accept()
            return
        super().mouseReleaseEvent(event)
