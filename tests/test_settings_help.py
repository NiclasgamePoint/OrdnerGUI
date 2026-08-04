from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)

from app.gui.settings_help import SettingsHelpController
from app.gui.settings_popup import SETTINGS_HELP_TEXTS, SettingsPopup


class SettingsHelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.resize(500, 300)
        self.first = QPushButton("Erste", self.owner)
        self.first.setGeometry(20, 20, 120, 30)
        self.second = QPushButton("Zweite", self.owner)
        self.second.setGeometry(160, 20, 120, 30)
        self.controller = SettingsHelpController(self.owner, delay_ms=30)
        self.controller.register("shared", "Gemeinsame Erklärung", self.first)
        self.controller.register("other", "Andere Erklärung", self.second)
        self.owner.show()
        self.owner.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.owner.setFocus()
        QTest.mouseMove(self.owner, QPoint(400, 250))
        self.app.processEvents()
        self.controller.hide()

    def tearDown(self):
        self.controller.dispose()
        self.owner.close()
        self.app.processEvents()

    @staticmethod
    def _send(widget, event_type):
        QApplication.sendEvent(widget, QEvent(event_type))

    def test_hover_waits_for_delay_and_stays_until_leave(self):
        self._send(self.first, QEvent.Type.Enter)
        QTest.qWait(15)
        self.assertFalse(self.controller.bubble.isVisible())
        QTest.qWait(25)
        self.assertTrue(self.controller.bubble.isVisible())
        self.assertEqual(self.controller.bubble.label.text(), "Gemeinsame Erklärung")
        QTest.qWait(40)
        self.assertTrue(self.controller.bubble.isVisible())
        self._send(self.first, QEvent.Type.Leave)
        QTest.qWait(5)
        self.assertFalse(self.controller.bubble.isVisible())

    def test_keyboard_focus_uses_the_same_delay(self):
        self.first.setFocus()
        QTest.qWait(40)
        self.assertTrue(self.controller.bubble.isVisible())
        self.owner.setFocus()
        self.app.processEvents()
        self.assertFalse(self.controller.bubble.isVisible())

    def test_switching_target_cancels_stale_help(self):
        self._send(self.first, QEvent.Type.Enter)
        QTest.qWait(15)
        self._send(self.first, QEvent.Type.Leave)
        self._send(self.second, QEvent.Type.Enter)
        QTest.qWait(20)
        self.assertFalse(self.controller.bubble.isVisible())
        QTest.qWait(20)
        self.assertTrue(self.controller.bubble.isVisible())
        self.assertEqual(self.controller.bubble.label.text(), "Andere Erklärung")

    def test_click_and_owner_hide_close_visible_help(self):
        self._send(self.first, QEvent.Type.Enter)
        QTest.qWait(40)
        self.assertTrue(self.controller.bubble.isVisible())
        QTest.mouseClick(self.first, Qt.MouseButton.LeftButton)
        self.assertFalse(self.controller.bubble.isVisible())
        self._send(self.first, QEvent.Type.FocusOut)
        self._send(self.first, QEvent.Type.Leave)
        self._send(self.second, QEvent.Type.Enter)
        self.app.processEvents()
        QTest.qWait(40)
        self.assertTrue(self.controller.bubble.isVisible())
        self.owner.hide()
        self.app.processEvents()
        self.assertFalse(self.controller.bubble.isVisible())

    def test_label_and_control_with_same_id_keep_one_timer(self):
        label = QPushButton("Label", self.owner)
        label.setGeometry(20, 80, 120, 30)
        field = QLineEdit(self.owner)
        field.setGeometry(160, 80, 180, 30)
        label.show()
        field.show()
        self.controller.register("row", "Zeilenerklärung", label, field)
        self._send(label, QEvent.Type.Enter)
        QTest.qWait(20)
        self._send(label, QEvent.Type.Leave)
        self._send(field, QEvent.Type.Enter)
        self.app.processEvents()
        QTest.qWait(15)
        self.assertTrue(self.controller.bubble.isVisible())
        self.assertEqual(self.controller.bubble.label.text(), "Zeilenerklärung")

    def test_help_bubble_is_kept_inside_available_screen(self):
        self._send(self.first, QEvent.Type.Enter)
        QTest.qWait(40)
        screen = QApplication.screenAt(self.controller.bubble.geometry().center())
        screen = screen or QApplication.primaryScreen()
        self.assertIsNotNone(screen)
        self.assertTrue(screen.availableGeometry().contains(
            self.controller.bubble.geometry()
        ))

    def test_every_setting_field_and_action_has_registered_help(self):
        popup = SettingsPopup("light", "#2db89d")
        self.assertEqual(popup.help_controller.delay_ms, 3000)
        candidates = []
        for widget_type in (
            QAbstractButton, QCheckBox, QComboBox, QLineEdit,
            QListWidget, QSlider, QSpinBox,
        ):
            candidates.extend(popup.stack.findChildren(widget_type))
        unique_candidates = set(candidates)
        missing = [
            widget
            for widget in unique_candidates
            if not str(widget.property("settingsHelpId") or "").strip()
        ]
        self.assertEqual(
            [(type(widget).__name__, getattr(widget, "text", lambda: "")()) for widget in missing],
            [],
        )
        self.assertEqual(popup.help_controller.registered_ids(), set(SETTINGS_HELP_TEXTS))
        for widget in unique_candidates:
            self.assertTrue(widget.accessibleDescription().strip())
        popup.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
