from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from app.core.customer_models import Customer
from app.core.config import CustomerRecognitionOptions
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs.centered_popup import CenteredPopupDialog
from app.gui.dialogs.customer_editor import CustomerEditorDialog
from app.gui.navigation import NavigationController
from app.gui.pages import FolderPage, SearchPage
from app.gui.settings_popup import SettingsPopup


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

    def test_customer_editor_shows_compact_folder_context_but_keeps_full_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "Blower Door" / "2026" / "Musterkunde"
            project.mkdir(parents=True)
            repository = CustomerRepository(root / "customers.db")
            customer = repository.save(Customer(
                display_name="Musterkunde",
                company="Musterkunde",
                folder_path=str(project),
                folder_paths=[str(project)],
            ))

            dialog = CustomerEditorDialog(
                repository,
                customer_id=customer.id,
            )
            name_item = dialog.selected_folders_table.item(0, 0)
            service_item = dialog.selected_folders_table.item(0, 1)
            year_item = dialog.selected_folders_table.item(0, 2)

            self.assertEqual(name_item.text(), "Musterkunde")
            self.assertEqual(service_item.text(), "Blower Door")
            self.assertEqual(year_item.text(), "2026")
            self.assertEqual(name_item.data(Qt.UserRole), str(project.resolve()))
            self.assertIn(str(project.resolve()), name_item.toolTip())
            self.assertNotIn(str(root), dialog.folder_paths.text())

            dialog.selected_folders_table.itemClicked.emit(service_item)
            self.assertEqual(dialog.selected_folders_table.rowCount(), 0)
            self.assertEqual(dialog.found_folders_table.rowCount(), 1)
            self.assertEqual(
                dialog.found_folders_table.item(0, 0).data(Qt.UserRole),
                str(project.resolve()),
            )

            dialog.found_folders_table.itemClicked.emit(
                dialog.found_folders_table.item(0, 2)
            )
            self.assertEqual(dialog.selected_folders_table.rowCount(), 1)
            self.assertEqual(dialog.found_folders_table.rowCount(), 0)

            dialog.close()
            repository.close()

    def test_customer_editor_assigns_current_folder_to_existing_customer(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "Energieberatung" / "2026" / "Projekt B"
            folder.mkdir(parents=True)
            repository = CustomerRepository(root / "customers.db")
            customer = repository.save(Customer(
                display_name="Bestandskunde",
                company="Bestandskunde",
            ))

            dialog = CustomerEditorDialog(
                repository,
                folder_path=str(folder),
                suggested_name="Projekt B",
            )
            self.assertIsNotNone(dialog.existing_customer_combo)
            dialog.existing_customer_combo.setCurrentIndex(0)
            dialog._assign_to_existing_customer()

            assigned = repository.get_by_folder(str(folder))
            self.assertIsNotNone(assigned)
            self.assertEqual(assigned.id, customer.id)
            self.assertIn("Energieberatung", assigned.service_types)

            dialog.close()
            repository.close()

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

    def test_folder_page_renders_nested_and_empty_subfolders_as_tree(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "Bilder" / "Innen"
            empty = root / "Leerer Ordner"
            nested.mkdir(parents=True)
            empty.mkdir()
            photo = nested / "foto.jpg"
            photo.write_bytes(b"image")

            page = FolderPage()
            page.set_folder({
                "folder_path": str(root),
                "folder_name": "Projekt",
                "file_count": 1,
                "total_size": photo.stat().st_size,
                "files": [{
                    "filename": photo.name,
                    "path": str(photo),
                    "file_type": "jpg",
                    "file_size": photo.stat().st_size,
                    "relative_dir": "Bilder/Innen",
                }],
                "subfolders": [
                    {
                        "path": str(root / "Bilder"),
                        "name": "Bilder",
                        "children": [{
                            "path": str(nested),
                            "name": "Innen",
                            "children": [],
                        }],
                    },
                    {
                        "path": str(empty),
                        "name": "Leerer Ordner",
                        "children": [],
                    },
                ],
            })

            self.assertEqual(page.file_list.topLevelItemCount(), 2)
            pictures = page.file_list.topLevelItem(0)
            self.assertEqual(pictures.text(0), "Bilder")
            self.assertEqual(pictures.child(0).text(0), "Innen")
            self.assertEqual(pictures.child(0).child(0).text(0), "foto.jpg")
            self.assertEqual(page.file_list.topLevelItem(1).text(0), "Leerer Ordner")
            page.cleanup()

    def test_settings_exposes_explicit_customer_recognition_page(self):
        popup = SettingsPopup(
            "dark",
            "#2db89d",
            recognition_options=CustomerRecognitionOptions(
                enabled=True,
                email_blacklist="intern@example.de",
            ),
            pending_recognition_cases=2,
        )

        self.assertEqual(popup.nav_list.count(), 4)
        self.assertEqual(
            [popup.nav_list.item(index).text() for index in range(popup.nav_list.count())],
            ["Allgemein", "Indexierung", "Kundenerkennung", "Aussehen"],
        )
        self.assertIn("Diagnose", popup.diagnostics_text.toPlainText())
        self.assertTrue(popup.clear_customer_data_button.isEnabled())
        self.assertTrue(popup.recognition_enabled.isChecked())
        self.assertEqual(
            popup.recognition_blacklist_fields["email_blacklist"].toPlainText(),
            "intern@example.de",
        )
        self.assertTrue(popup.review_recognition_button.isEnabled())
        self.assertIn("2", popup.review_recognition_button.text())
        popup.close()


if __name__ == "__main__":
    unittest.main()
