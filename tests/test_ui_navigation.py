from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from app.core.customer_models import Customer
from app.gui.dialogs.centered_popup import CenteredPopupDialog
from app.gui.navigation import NavigationController
from app.gui.pages import FolderPage, SearchPage


class UiNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_navigation_controller_restores_previous_route(self):
        navigator = NavigationController()
        navigator.navigate("customer", 7)
        navigator.navigate("folder", "/tmp/example")
        self.assertTrue(navigator.can_go_back)

        navigator.back()
        self.assertEqual(navigator.current.page, "customer")
        self.assertEqual(navigator.current.payload, 7)

        navigator.back()
        self.assertEqual(navigator.current.page, "search")
        self.assertFalse(navigator.can_go_back)

    def test_centered_popup_is_modal_and_centered_over_owner(self):
        owner = QWidget()
        owner.setGeometry(40, 30, 700, 500)
        owner.show()
        popup = CenteredPopupDialog(owner)
        popup.resize(500, 360)
        popup.show()
        self.app.processEvents()
        popup.center_on_parent()

        owner_center = owner.mapToGlobal(owner.rect().center())
        popup_center = popup.frameGeometry().center()
        self.assertLessEqual(abs(owner_center.x() - popup_center.x()), 1)
        self.assertLessEqual(abs(owner_center.y() - popup_center.y()), 1)
        self.assertTrue(popup.isModal())
        self.assertTrue(popup.windowFlags() & Qt.FramelessWindowHint)

        popup.close()
        owner.close()

    def test_search_page_separates_customer_and_folder_rows(self):
        page = SearchPage()
        customer = Customer(
            id=12,
            display_name="Muster GmbH",
            entity_type="Unternehmen",
            folder_path="/data/Muster GmbH",
            folder_paths=["/data/Muster GmbH"],
        )
        page.set_customers([customer], 1)
        page.set_folders(
            [{
                "folder_name": "Projekt A",
                "folder_path": "/data/Projekt A",
                "relative_path": "2026/Projekt A",
                "file_count": 4,
            }],
            1,
        )

        self.assertEqual(page.customer_section.row_count, 1)
        self.assertEqual(page.folder_section.row_count, 1)

    def test_folder_page_populates_only_matching_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "eins.pdf"
            second = root / "zwei.txt"
            first.write_bytes(b"%PDF")
            second.write_text("Text", encoding="utf-8")

            page = FolderPage()
            page.set_folder({
                "folder_path": str(root),
                "folder_name": "Testordner",
                "file_count": 2,
                "total_size": first.stat().st_size + second.stat().st_size,
                "service_types": ["DEKRA"],
                "files": [
                    {
                        "filename": first.name,
                        "path": str(first),
                        "relative_dir": "",
                    },
                    {
                        "filename": second.name,
                        "path": str(second),
                        "relative_dir": "",
                    },
                ],
            })
            self.assertEqual(page.file_list.count(), 2)
            page.file_filter.setText("zwei")
            self.assertEqual(page.file_list.count(), 1)
            page.cleanup()


if __name__ == "__main__":
    unittest.main()
