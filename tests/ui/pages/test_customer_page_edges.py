from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.core.customer_models import Customer, CustomerJournalEntry
from app.core.customer_models import CustomerProject
from app.core.customer_repository import CustomerRepository
from app.gui.pages.customer_page import (
    CustomerPage,
    _JournalEntryCard,
    _JournalEntryEditorDialog,
)


class CustomerPageEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = CustomerRepository(Path(self.temporary.name) / "customers.db")
        self.addCleanup(self.repository.close)
        self.page = CustomerPage(self.repository)
        self.addCleanup(self.page.close)

    def test_journal_editor_card_dates_and_all_result_paths(self):
        editor = _JournalEntryEditorDialog(" title ", " body ")
        self.assertEqual(editor.values(), ("title", "body"))
        editor.close()
        no_title = _JournalEntryCard(CustomerJournalEntry(
            entry_number=1, body="Body", created_at="bad date"
        ))
        self.assertEqual(CustomerPage._format_journal_date(""), "-")
        self.assertEqual(CustomerPage._format_journal_date("bad"), "bad")
        self.assertIn("2026", CustomerPage._format_journal_date("2026-01-02T03:04:00"))
        no_title.close()

        self.page._add_journal_entry()
        entry = CustomerJournalEntry(id=None, entry_number=1, body="Body")
        self.page._open_journal_context_menu(entry, QPoint())
        self.page._edit_journal_entry(entry)
        self.page._delete_journal_entry(entry)

        customer = self.repository.save(Customer(display_name="Customer"))
        self.page.set_customer(customer)
        self.page._save_notes()
        self.page.journal_entries_layout.insertSpacing(0, 1)
        self.page._reload_journal_entries()
        self.page.journal_input.clear()
        self.page._add_journal_entry()
        self.assertIn("Text", self.page.journal_status.text())
        persisted = self.repository.add_journal_entry(int(customer.id), "Body", "Title")

        fake_editor = Mock()
        fake_editor.exec.return_value = QDialog.DialogCode.Rejected
        with patch(
            "app.gui.pages.customer_page._JournalEntryEditorDialog", return_value=fake_editor
        ):
            self.page._edit_journal_entry(persisted)
        fake_editor.exec.return_value = QDialog.DialogCode.Accepted
        fake_editor.values.return_value = ("New", "New body")
        with patch(
            "app.gui.pages.customer_page._JournalEntryEditorDialog", return_value=fake_editor
        ), patch.object(self.repository, "update_journal_entry", return_value=None):
            self.page._edit_journal_entry(persisted)
        self.assertIn("nicht", self.page.journal_status.text())
        with patch(
            "app.gui.pages.customer_page._JournalEntryEditorDialog", return_value=fake_editor
        ):
            self.page._edit_journal_entry(persisted)
        self.assertIn("aktualisiert", self.page.journal_status.text())

        class FakeMenu:
            selection = "Bearbeiten"

            def __init__(self, _parent):
                self.actions = []

            def addAction(self, text):
                action = SimpleNamespace(text=text)
                self.actions.append(action)
                return action

            def exec(self, _position):
                if self.selection is None:
                    return None
                return next(action for action in self.actions if action.text == self.selection)

        with patch("app.gui.pages.customer_page.QMenu", FakeMenu), \
                patch.object(self.page, "_edit_journal_entry") as edit:
            self.page._open_journal_context_menu(persisted, QPoint())
        edit.assert_called_once()
        FakeMenu.selection = "Löschen"
        with patch("app.gui.pages.customer_page.QMenu", FakeMenu), \
                patch.object(self.page, "_delete_journal_entry") as delete:
            self.page._open_journal_context_menu(persisted, QPoint())
        delete.assert_called_once()
        FakeMenu.selection = None
        with patch("app.gui.pages.customer_page.QMenu", FakeMenu):
            self.page._open_journal_context_menu(persisted, QPoint())

        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            self.page._delete_journal_entry(persisted)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                patch.object(self.repository, "delete_journal_entry", return_value=False):
            self.page._delete_journal_entry(persisted)
        self.assertIn("nicht", self.page.journal_status.text())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.page._delete_journal_entry(persisted)

    def test_notes_projects_fallbacks_and_resizing(self):
        self.page._save_notes()
        customer = Customer(
            display_name="Loose", folder_paths=[
                "/Service/2026/Project", "/NoYear/Other"
            ], service_types=[""],
        )
        self.page.set_customer(customer, {
            "/Service/2026/Project": {"last_modified": "bad", "file_count": 2},
        })
        self.page.notes.setPlainText("Note")
        self.page._notes_sync_in_progress = True
        self.page._save_notes()
        self.page._notes_sync_in_progress = False
        self.assertEqual(len(self.page._projects_for_customer(customer)), 2)
        self.assertEqual(self.page._project_from_folder("", "").project_label, "")
        self.assertEqual(
            self.page._project_from_folder("2026/Project", "Service").year, 2026
        )
        self.assertEqual(self.page._project_from_folder("/Service/2026", "").year, 2026)
        with patch.object(self.page, "_projects_for_customer", return_value=[
            CustomerProject(folder_path="/x", project_label="", service_type="")
        ]):
            self.page._set_projects(customer, {})

        with patch.object(CustomerPage, "width", return_value=800):
            self.page.resizeEvent(QResizeEvent(QSize(800, 500), QSize(1000, 500)))
        self.assertEqual(self.page.splitter.orientation(), Qt.Vertical)
        with patch.object(CustomerPage, "width", return_value=1000):
            self.page.resizeEvent(QResizeEvent(QSize(1000, 500), QSize(800, 500)))
            self.page.resizeEvent(QResizeEvent(QSize(1000, 500), QSize(1000, 500)))

    def test_edit_review_reload_and_suggestion_branches(self):
        self.page._edit_customer()
        self.page._review_suggestions()
        self.page._reload_customer(9999)
        self.page._refresh_suggestion_count()

        customer = self.repository.save(Customer(display_name="Customer"))
        self.page.set_customer(customer)
        editor = Mock()
        editor.exec.return_value = 0
        with patch("app.gui.pages.customer_page.CustomerEditorDialog", return_value=editor):
            self.page._edit_customer()
        editor.exec.return_value = 1
        with patch("app.gui.pages.customer_page.CustomerEditorDialog", return_value=editor), \
                patch.object(self.repository, "get", return_value=None):
            self.page._edit_customer()
        with patch("app.gui.pages.customer_page.CustomerEditorDialog", return_value=editor):
            self.page._edit_customer()

        signal = SimpleNamespace(connect=Mock())
        review = Mock(suggestionsChanged=signal, customerChanged=signal)
        with patch(
            "app.gui.pages.customer_page.CustomerDataSuggestionsDialog", return_value=review
        ):
            self.page._review_suggestions()
        review.exec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
