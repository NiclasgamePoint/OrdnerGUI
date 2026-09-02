from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from app.index_tray_app import StandaloneIndexTray, build_parser
from tests.base.test_case import PapaGuiTestCase


class StandaloneIndexTrayTests(PapaGuiTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_parser_supports_background_start(self):
        self.assertTrue(build_parser().parse_args(["--background"]).background)
        self.assertFalse(build_parser().parse_args([]).background)

    def test_controller_owns_window_and_tray_without_main_window(self):
        window = Mock()
        with (
            patch("app.index_tray_app.IndexTrayWindow", return_value=window) as window_type,
            patch("app.index_tray_app.QSystemTrayIcon.isSystemTrayAvailable", return_value=False),
        ):
            controller = StandaloneIndexTray(self.app, show_window=True)

        window_type.assert_called_once()
        window.show_status.assert_called_once_with()
        controller._activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertEqual(window.show_status.call_count, 2)
        controller._activated(QSystemTrayIcon.ActivationReason.Unknown)
        self.assertEqual(window.show_status.call_count, 2)
        controller.shutdown()
        self.assertFalse(controller.tray_icon.isVisible())
        window.shutdown.assert_called_once_with()


if __name__ == "__main__":
    import unittest

    unittest.main()
