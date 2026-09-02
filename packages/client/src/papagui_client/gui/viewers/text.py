"""Searchable plain-text preview widget."""

from __future__ import annotations

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from .buttons import viewer_button


class TextViewerWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerContent")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Im Dokument suchen …")
        self.search_input.setMaximumWidth(220)
        self.search_button = viewer_button("Weiter")
        self.search_input.returnPressed.connect(self.find_next)
        self.search_button.clicked.connect(self.find_next)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.search_button)
        layout.addLayout(toolbar)
        self.editor = QTextEdit()
        self.editor.setReadOnly(True)
        layout.addWidget(self.editor, 1)

    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)

    def find_next(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            return
        if not self.editor.find(query):
            self.editor.moveCursor(QTextCursor.MoveOperation.Start)
            self.editor.find(query)
