from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog


class CenteredPopupDialog(QDialog):
    """Frameless modal dialog centered over its owning application window."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.Dialog | Qt.FramelessWindowHint,
        )
        self.setObjectName("CenteredPopupDialog")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModal)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.center_on_parent)

    def center_on_parent(self):
        owner = self.parentWidget()
        owner_window = owner.window() if owner is not None else None
        if owner_window is not None:
            center = owner_window.mapToGlobal(owner_window.rect().center())
            screen = owner_window.screen()
        else:
            screen = QApplication.primaryScreen()
            center = (
                screen.availableGeometry().center()
                if screen is not None
                else self.rect().center()
            )

        target_x = center.x() - self.width() // 2
        target_y = center.y() - self.height() // 2
        if screen is not None:
            available = screen.availableGeometry()
            target_x = max(
                available.left(),
                min(target_x, available.right() - self.width() + 1),
            )
            target_y = max(
                available.top(),
                min(target_y, available.bottom() - self.height() + 1),
            )
        self.move(target_x, target_y)

