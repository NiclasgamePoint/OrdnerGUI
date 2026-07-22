from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar

from app.gui.widgets.buttons import AppButton


class IndexStatusBar(QFrame):
    """Compact persistent status and progress area."""

    cancelRequested = Signal()
    detailsRequested = Signal()
    textChanged = Signal(str)
    busyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("IndexStatusBar")
        self.setMinimumHeight(38)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip("Indexdiagnose öffnen")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 8, 5)
        layout.setSpacing(8)

        self.status_label = QLabel("Bereit")
        self.status_label.setObjectName("IndexStatusText")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.cancel_button = AppButton(
            "Abbrechen",
            AppButton.SECONDARY,
            minimum_width=88,
        )
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancelRequested.emit)

        layout.addWidget(self.status_label, 1)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.cancel_button)

    def set_text(self, text: str):
        self.status_label.setText(text)
        self.textChanged.emit(text)

    def set_busy(self, busy: bool):
        self.progress_bar.setVisible(busy)
        self.cancel_button.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
        self.busyChanged.emit(busy)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.detailsRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
