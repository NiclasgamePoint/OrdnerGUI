from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class SettingsHelpBubble(QFrame):
    """Passive, theme-aware help bubble that never steals input focus."""

    MAXIMUM_TEXT_WIDTH = 360
    SCREEN_MARGIN = 8

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("SettingsHelpBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.label = QLabel()
        self.label.setObjectName("SettingsHelpText")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(self.MAXIMUM_TEXT_WIDTH)
        layout.addWidget(self.label)

    def show_for(self, anchor: QWidget, text: str):
        self.label.setText(text)
        self.adjustSize()
        anchor_bottom = anchor.mapToGlobal(QPoint(0, anchor.height()))
        screen = QGuiApplication.screenAt(anchor_bottom) or QGuiApplication.primaryScreen()
        if screen is None:
            position = anchor_bottom + QPoint(0, 4)
        else:
            area = screen.availableGeometry().adjusted(
                self.SCREEN_MARGIN,
                self.SCREEN_MARGIN,
                -self.SCREEN_MARGIN,
                -self.SCREEN_MARGIN,
            )
            x = min(max(anchor_bottom.x(), area.left()), area.right() - self.width() + 1)
            below = anchor_bottom.y() + 4
            above = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
            y = below if below + self.height() <= area.bottom() else above
            y = min(max(y, area.top()), area.bottom() - self.height() + 1)
            position = QPoint(x, y)
        self.move(position)
        self.show()
        self.raise_()


class SettingsHelpController(QObject):
    """Show registered setting help after a platform-independent delay."""

    DEFAULT_DELAY_MS = 3000

    def __init__(self, owner: QWidget, delay_ms: int = DEFAULT_DELAY_MS):
        super().__init__(owner)
        self.owner = owner
        self.delay_ms = max(0, int(delay_ms))
        self.bubble = SettingsHelpBubble()
        self.bubble.setParent(owner, self.bubble.windowFlags())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_pending)
        self._watched_roots: dict[QObject, QWidget] = {}
        self._help: dict[QWidget, tuple[str, str]] = {}
        self._hovered: set[QObject] = set()
        self._focused: set[QObject] = set()
        self._pending_id = ""
        self._pending_anchor: QWidget | None = None
        self._visible_id = ""
        self._suppressed_id = ""
        self._disposed = False
        owner.installEventFilter(self)

    def set_delay_ms(self, delay_ms: int):
        self.delay_ms = max(0, int(delay_ms))

    def register(self, help_id: str, text: str, *widgets: QWidget):
        normalized_id = str(help_id).strip()
        normalized_text = str(text).strip()
        if not normalized_id or not normalized_text:
            raise ValueError("Einstellungshilfe benötigt ID und Text")
        for widget in widgets:
            widget.setProperty("settingsHelpId", normalized_id)
            widget.setAccessibleDescription(normalized_text)
            self._help[widget] = (normalized_id, normalized_text)
            self._watch(widget, widget)
            for child in widget.findChildren(QWidget):
                self._watch(child, widget)

    def registered_ids(self) -> set[str]:
        return {help_id for help_id, _text in self._help.values()}

    def hide(self):
        if self._disposed:
            return
        self._timer.stop()
        self._pending_id = ""
        self._pending_anchor = None
        self._visible_id = ""
        if self.bubble is not None:
            self.bubble.hide()

    def eventFilter(self, watched, event):
        if getattr(self, "_disposed", True):
            return False
        event_type = event.type()
        if watched is getattr(self, "owner", None) and event_type in {
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.Destroy,
        }:
            self.hide()
            return False
        root = self._watched_roots.get(watched)
        if root is None:
            return False
        if event_type == QEvent.Type.Enter:
            self._hovered.add(watched)
            self._activate(root)
        elif event_type == QEvent.Type.Leave:
            self._hovered.discard(watched)
            QTimer.singleShot(0, self._hide_if_inactive)
        elif event_type == QEvent.Type.FocusIn:
            self._focused.add(watched)
            self._activate(root)
        elif event_type == QEvent.Type.FocusOut:
            self._focused.discard(watched)
            QTimer.singleShot(0, self._hide_if_inactive)
        elif event_type == QEvent.Type.MouseButtonPress:
            help_id = self._help[root][0]
            self._suppressed_id = help_id
            self.hide()
        return False

    def _watch(self, watched: QObject, root: QWidget):
        if isinstance(watched, QWidget):
            help_id, text = self._help[root]
            watched.setProperty("settingsHelpId", help_id)
            watched.setAccessibleDescription(text)
        watched.installEventFilter(self)
        self._watched_roots[watched] = root

    def _activate(self, root: QWidget):
        help_id, _text = self._help[root]
        if self._suppressed_id == help_id:
            return
        if self._visible_id == help_id or (
            self._pending_id == help_id and self._timer.isActive()
        ):
            return
        self.hide()
        self._pending_id = help_id
        self._pending_anchor = root
        self._timer.start(self.delay_ms)

    def _show_pending(self):
        if self._disposed or self.bubble is None:
            return
        anchor = self._pending_anchor
        if anchor is None or not self._is_active(anchor) or not anchor.isVisible():
            self.hide()
            return
        help_id, text = self._help[anchor]
        self._visible_id = help_id
        self.bubble.show_for(anchor, text)

    def _hide_if_inactive(self):
        active_roots = {
            self._watched_roots[watched]
            for watched in self._hovered | self._focused
            if watched in self._watched_roots
        }
        active_ids = {self._help[root][0] for root in active_roots if root in self._help}
        current_id = self._visible_id or self._pending_id
        if current_id and current_id in active_ids:
            return
        if self._suppressed_id and self._suppressed_id not in active_ids:
            self._suppressed_id = ""
        self.hide()

    def _is_active(self, root: QWidget) -> bool:
        help_id = self._help[root][0]
        active_roots = {
            self._watched_roots[watched]
            for watched in self._hovered | self._focused
            if watched in self._watched_roots
        }
        return any(
            self._help[candidate][0] == help_id
            for candidate in active_roots
            if candidate in self._help
        )

    def dispose(self):
        if self._disposed:
            return
        self.hide()
        self._disposed = True
        for watched in tuple(self._watched_roots):
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched_roots.clear()
        self._help.clear()
        try:
            self.owner.removeEventFilter(self)
        except RuntimeError:
            pass
        bubble = self.bubble
        self.bubble = None
        if bubble is not None:
            bubble.deleteLater()

