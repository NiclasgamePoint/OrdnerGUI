from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QWidget


class AppButton(QPushButton):
    """Reusable application button with themeable semantic roles."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    DANGER = "danger"

    def __init__(
        self,
        text: str = "",
        role: str = PRIMARY,
        parent: QWidget | None = None,
        minimum_width: int | None = None,
    ):
        super().__init__(text, parent)
        self.setProperty("buttonRole", role)
        self._enabled_before_busy = True
        self._busy = False
        if minimum_width is not None:
            self.setMinimumWidth(minimum_width)

    def set_role(self, role: str):
        self.setProperty("buttonRole", role)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_busy(self, busy: bool):
        if busy == self._busy:
            return
        self._busy = busy
        if busy:
            self._enabled_before_busy = self.isEnabled()
            self.setEnabled(False)
        else:
            self.setEnabled(self._enabled_before_busy)


class BusyIndicator(QLabel):
    """Small asset-free animated indicator suitable for background work."""

    FRAMES = ("◐", "◓", "◑", "◒")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("BusyIndicator")
        self.setFixedWidth(22)
        self.setToolTip("Vorgang läuft im Hintergrund")
        self._frame_index = 0
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._advance)
        self.setVisible(False)

    def start(self):
        self._frame_index = 0
        self.setText(self.FRAMES[0])
        self.setVisible(True)
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.clear()
        self.setVisible(False)

    def is_running(self) -> bool:
        return self._timer.isActive()

    def _advance(self):
        self._frame_index = (self._frame_index + 1) % len(self.FRAMES)
        self.setText(self.FRAMES[self._frame_index])
