from __future__ import annotations

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from app.gui.widgets.buttons import AppButton


class TextViewerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Im Dokument suchen …")
        self.search_input.setMaximumWidth(220)
        self.search_button = AppButton("Weiter", AppButton.SECONDARY)
        self.search_input.returnPressed.connect(self.find_next)
        self.search_button.clicked.connect(self.find_next)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.search_button)
        layout.addLayout(toolbar)
        self.editor = QTextEdit()
        self.editor.setReadOnly(True)
        layout.addWidget(self.editor, 1)

    def set_text(self, text: str):
        self.editor.setPlainText(text)
        self.editor.moveCursor(QTextCursor.Start)

    def find_next(self):
        query = self.search_input.text().strip()
        if not query:
            return
        if not self.editor.find(query):
            self.editor.moveCursor(QTextCursor.Start)
            self.editor.find(query)
