"""Settings controls whose values can only be wheeled after a deliberate click."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QListView, QSlider, QSpinBox, QStyledItemDelegate


class _ClickActivatedWheel:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._wheel_adjustment_enabled = False
        # Keep keyboard navigation, but never acquire focus just by scrolling.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._wheel_editor = self.lineEdit() if isinstance(self, (QSpinBox, QComboBox)) else None
        if self._wheel_editor is not None:
            self._wheel_editor.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            watched is self._wheel_editor
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._wheel_adjustment_enabled = True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._wheel_adjustment_enabled = True
            # macOS does not always focus a combo on mouse selection. Focus it
            # before opening the popup so Qt can return focus after selection.
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        # A combo's own popup temporarily takes focus during explicit selection.
        own_popup_active = (
            isinstance(self, QComboBox)
            and QApplication.activePopupWidget() is self.view().window()
        )
        if event.reason() != Qt.FocusReason.PopupFocusReason and not own_popup_active:
            self._wheel_adjustment_enabled = False
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._wheel_adjustment_enabled = False
        super().hideEvent(event)

    def wheelEvent(self, event):
        if not self._wheel_adjustment_enabled or not self.hasFocus():
            # Let Qt deliver the wheel to the surrounding scroll area.
            event.ignore()
            return
        super().wheelEvent(event)


class ClickActivatedSpinBox(_ClickActivatedWheel, QSpinBox):
    """A number input protected against accidental changes while scrolling."""


class ClickActivatedComboBox(_ClickActivatedWheel, QComboBox):
    """A selection input protected against accidental changes while scrolling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Native menu delegates can draw white selected text without the QSS
        # background. Use the same styled rows on every desktop platform.
        view = QListView(self)
        view.setItemDelegate(QStyledItemDelegate(view))
        self.setView(view)

    def hidePopup(self):
        restore_focus = self._wheel_adjustment_enabled and self.view().isVisible()
        super().hidePopup()
        if restore_focus and self.isVisible():
            self.setFocus(Qt.FocusReason.PopupFocusReason)


class ClickActivatedSlider(_ClickActivatedWheel, QSlider):
    """A slider protected against accidental changes while scrolling."""
