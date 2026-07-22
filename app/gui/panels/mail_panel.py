from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class MailPanel(QWidget):
    """Placeholder panel for the future mail integration in folder view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MailPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel("Mails")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.info_label = QLabel(
            "Die Mail-Ansicht ist vorbereitet. "
            "Sobald ein Postfach angebunden ist, erscheinen hier Nachrichten und Anhaenge."
        )
        self.info_label.setObjectName("SearchSectionMessage")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self.info_label)
        layout.addStretch(1)
