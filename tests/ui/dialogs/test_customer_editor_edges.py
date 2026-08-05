from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem

from app.core.customer_models import Contact, Customer
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs.customer_editor import CustomerEditorDialog


class CustomerEditorEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = CustomerRepository(Path(self.temporary.name) / "customers.db")
        self.addCleanup(self.repository.close)

    def test_assignment_validation_failures_and_contact_table_edges(self):
        folder = str(Path(self.temporary.name) / "Service" / "2026" / "Project")
        self.repository.save(Customer(display_name="Existing", company="Existing"))
        dialog = CustomerEditorDialog(self.repository, folder_path=folder, suggested_name="New")
        with patch.object(QMessageBox, "warning") as warning:
            dialog._assign_to_existing_customer()
        warning.assert_called_once()

        dialog.existing_customer_combo.setCurrentIndex(0)
        dialog.context_folder_path = str(Path(self.temporary.name) / "Service" / "2015" / "Old")
        with patch.object(QMessageBox, "warning") as warning:
            dialog._assign_to_existing_customer()
        warning.assert_called_once()

        dialog.context_folder_path = folder
        with patch.object(
            self.repository, "add_folder_to_customer", side_effect=ValueError("owned")
        ), patch.object(QMessageBox, "warning") as warning:
            dialog._assign_to_existing_customer()
        self.assertIn("owned", warning.call_args.args[-1])

        dialog.existing_customer_combo = None
        dialog._assign_to_existing_customer()
        dialog._append_contact(Contact(name="Auto"), auto_filled=True)
        dialog.contacts_table.setCurrentCell(0, 0)
        dialog._remove_contact()
        dialog.contacts_table.clearSelection()
        dialog.contacts_table.setCurrentCell(-1, -1)
        dialog._remove_contact()
        dialog.close()

        with patch.object(self.repository, "list_customer_types", return_value=[]), \
                patch("app.gui.dialogs.customer_editor.QComboBox.completer", return_value=None):
            fallback = CustomerEditorDialog(self.repository, folder_path=folder)
        self.assertGreater(fallback.entity_type.count(), 0)
        fallback.close()

    def test_folder_helpers_discovery_and_move_guards(self):
        dialog = CustomerEditorDialog(self.repository, suggested_name="X")
        dialog._move_folder_item(None, dialog.selected_folders_table, dialog.found_folders_table, True)
        empty = QTableWidgetItem("")
        dialog.selected_folders_table.insertRow(0)
        dialog.selected_folders_table.setItem(0, 0, empty)
        dialog._move_folder_item(empty, dialog.selected_folders_table, dialog.found_folders_table, True)

        path = "/Service/2026/Project"
        dialog._append_folder_row(dialog.selected_folders_table, path, False)
        dialog._append_folder_row(dialog.found_folders_table, path, True)
        item = dialog.found_folders_table.item(0, 0)
        dialog._move_folder_item(item, dialog.found_folders_table, dialog.selected_folders_table, False)
        self.assertTrue(dialog._table_contains_path(dialog.selected_folders_table, path))
        self.assertFalse(dialog._table_contains_path(dialog.selected_folders_table, "/missing"))
        self.assertIsNone(dialog._extract_year_from_folder("/without/year"))

        manager = Mock()
        manager.search_folders_page.return_value = SimpleNamespace(items=[
            {"folder_path": "/Service/2027/Other", "relative_path": "Service/2027/Other"},
            {"folder_path": ""},
        ])
        with patch("app.gui.dialogs.customer_editor.IndexManager", return_value=manager):
            results = dialog._discover_related_folders("Other")
        self.assertIn("/Service/2027/Other", results)
        manager.close.assert_called_once()
        with patch("app.gui.dialogs.customer_editor.IndexManager", side_effect=RuntimeError("bad")):
            self.assertEqual(dialog._discover_related_folders("Other"), [])
        dialog.context_folder_path = "/"
        with patch("app.gui.dialogs.customer_editor.Path.resolve", side_effect=OSError("bad")), \
                patch("app.gui.dialogs.customer_editor.IndexManager", return_value=manager):
            dialog._discover_related_folders("")

        self.assertEqual(dialog._folder_display_info("/x/Vorlagen/Name").year, "Vorlagen")
        self.assertEqual(dialog._folder_display_info("2026/Name").service, "")
        self.assertEqual(dialog._folder_display_info("/x", "Service").service, "Service")
        self.assertFalse(dialog._folder_matches_customer_name("/", "Customer"))
        self.assertEqual(dialog._normalize_lookup_token(" A-B_(C). "), "a b c")
        self.assertEqual(dialog._parse_folder_values(""), [])
        self.assertEqual(dialog._parse_folder_values("a | b"), ["a", "b"])
        self.assertEqual(dialog._parse_folder_values("one"), ["one"])
        self.assertEqual(
            dialog._normalize_folder_values(["", "/absolute/path", "City"]),
            ["/absolute/path, City"],
        )
        dialog.close()

    def test_suggestion_and_save_delete_validation_paths(self):
        dialog = CustomerEditorDialog(self.repository, suggested_name="")
        empty = SimpleNamespace(has_values=lambda: False)
        with patch.object(dialog._suggestion_service, "suggest_for_folder", return_value=empty):
            dialog._offer_auto_suggestions("")

        suggestion = SimpleNamespace(
            has_values=lambda: True, display_name="Name", company="Company",
            entity_type="Organisation", street="Street", postal_code="12345",
            city="City", service_types=["One", "One", "Two"],
            contacts=[Contact(), Contact(name="Person", email="p@example.test")],
        )
        with patch.object(dialog._suggestion_service, "suggest_for_folder", return_value=suggestion):
            dialog._offer_auto_suggestions("Name")
        self.assertEqual(dialog.company.text(), "Company")
        minimal = SimpleNamespace(
            has_values=lambda: True, display_name="", company="", entity_type="",
            street="", postal_code="", city="", service_types=[], contacts=[],
        )
        with patch.object(dialog._suggestion_service, "suggest_for_folder", return_value=minimal):
            dialog._offer_auto_suggestions("")

        dialog.company.clear()
        with patch.object(QMessageBox, "warning") as warning:
            dialog._save()
        warning.assert_called_once()

        dialog.context_folder_path = ""
        dialog.contacts_table.insertRow(dialog.contacts_table.rowCount())
        dialog.company.setText("Company")
        with patch.object(self.repository, "save", return_value=dialog.customer):
            dialog._save()
        dialog.company.setText("Company")
        dialog.context_folder_path = "/Service/2026/Project"
        dialog.selected_folders_table.setRowCount(0)
        with patch.object(QMessageBox, "warning") as warning:
            dialog._save()
        warning.assert_called_once()

        saved = self.repository.save(Customer(display_name="Delete", company="Delete"))
        dialog.customer = saved
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
                patch.object(self.repository, "delete") as delete:
            dialog._delete_customer()
        delete.assert_not_called()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                patch.object(self.repository, "delete") as delete:
            dialog._delete_customer()
        delete.assert_called_once_with(saved.id)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
