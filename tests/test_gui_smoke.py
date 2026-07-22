from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.core.config import CustomerRecognitionOptions
from app.core.customer_repository import CustomerRepository
from app.core.index_manager import IndexManager
from app.gui.dialogs.customer_recognition_review import CustomerRecognitionReviewDialog
from app.gui.main_window import MainWindow
from app.gui.settings_popup import SettingsPopup


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_smoke_without_auto_index(self):
        with patch.object(MainWindow, "check_and_index", lambda self: None), patch.object(
            MainWindow, "_start_filesystem_monitor", lambda self: None
        ):
            window = MainWindow()
            self.assertIsNotNone(window.search_page)
            self.assertIsNotNone(window.customer_page)
            self.assertIsNotNone(window.folder_page)
            window.close()

    def test_customer_recognition_review_dialog_smoke(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "index.db"
            customers_path = root / "customers.db"
            with IndexManager(index_path) as _manager:
                pass
            repository = CustomerRepository(customers_path)
            repository.close()

            dialog = CustomerRecognitionReviewDialog(
                index_path=index_path,
                customer_database_path=customers_path,
                options=CustomerRecognitionOptions(enabled=True),
            )
            self.assertIsNotNone(dialog.case_list)
            self.assertIsNotNone(dialog.customer_combo)
            dialog.close()

    def test_settings_popup_smoke(self):
        popup = SettingsPopup("light", "#2db89d")
        self.assertEqual(popup.nav_list.count(), 4)
        popup.close()


if __name__ == "__main__":
    unittest.main()