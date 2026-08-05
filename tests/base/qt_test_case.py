"""Qt lifecycle support shared by widget and workflow tests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tests.base.test_case import PapaGuiTestCase


class QtTestCase(PapaGuiTestCase):
    """Reuse one QApplication and close widgets after every test."""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        super().tearDown()
