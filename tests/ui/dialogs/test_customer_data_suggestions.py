from __future__ import annotations

from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from app.core.config import CustomerRecognitionOptions
from app.core.customer_models import Contact, Customer, CustomerDataSuggestion
from app.gui.dialogs.customer_data_suggestions import CustomerDataSuggestionsDialog
from tests.base.qt_test_case import QtTestCase


class CustomerDataSuggestionsDialogTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.repository = Mock(database_path=self.temp_path / "customers.db")
        self.repository.list_data_suggestions.return_value = []
        self.repository.get.return_value = Customer(id=7)
        self.dialog = CustomerDataSuggestionsDialog(
            self.repository, 7, self.temp_path / "index.db",
            CustomerRecognitionOptions(),
        )
        self.addCleanup(self.dialog.deleteLater)

    def test_reload_and_cards_cover_fields_contacts_details_and_entity_type(self):
        field = CustomerDataSuggestion(
            id=1, field_name="email", suggested_value="new@example.test",
            rule="document", source_path="/tmp/source.pdf", excerpt="context",
            confidence=0.8,
        )
        contact = CustomerDataSuggestion(
            id=2, suggestion_type="contact", contact_name="Max",
            contact_role="Lead", contact_email="max@example.test",
            contact_phone="123", confidence=0.9,
        )
        entity = CustomerDataSuggestion(
            id=3, field_name="entity_type", suggested_value="Organisation",
            confidence=0.7,
        )
        self.repository.list_data_suggestions.return_value = [field, contact, entity]
        self.repository.get.return_value = Customer(id=7, entity_type="Unternehmen")
        self.dialog._reload()
        self.assertGreater(self.dialog.suggestion_layout.count(), 3)

        sparse = CustomerDataSuggestion(
            id=4, suggestion_type="contact", contact_name="Empty", confidence=0.1,
        )
        self.dialog._suggestion_card(sparse)
        unknown = CustomerDataSuggestion(
            id=5, field_name="unknown", suggested_value="value", confidence=0.1,
        )
        self.dialog._suggestion_card(unknown)
        self.repository.get.return_value = None
        self.dialog._suggestion_card(entity)

    def test_overwrite_resolution_confirmation_success_and_value_error(self):
        suggestion = CustomerDataSuggestion(
            id=1, field_name="email", suggested_value="new@example.test"
        )
        self.repository.get.return_value = Customer(id=7, email="old@example.test")
        self.assertTrue(self.dialog._would_overwrite(suggestion))
        self.assertFalse(self.dialog._would_overwrite(CustomerDataSuggestion(
            suggestion_type="contact"
        )))
        self.assertFalse(self.dialog._would_overwrite(CustomerDataSuggestion(
            field_name="contact_name"
        )))
        self.repository.get.return_value = None
        self.assertFalse(self.dialog._would_overwrite(suggestion))

        self.repository.get.return_value = Customer(id=7, email="old@example.test")
        with patch("app.gui.dialogs.customer_data_suggestions.QMessageBox.question", return_value=QMessageBox.No):
            self.dialog._resolve(suggestion, True)
        self.repository.resolve_data_suggestion.assert_not_called()

        changed = Mock()
        self.dialog.customerChanged.connect(changed)
        self.repository.resolve_data_suggestion.return_value = Customer(id=7)
        with patch("app.gui.dialogs.customer_data_suggestions.QMessageBox.question", return_value=QMessageBox.Yes):
            self.dialog._resolve(suggestion, True)
        changed.assert_called_with(7)

        self.repository.resolve_data_suggestion.side_effect = ValueError("stale")
        with patch("app.gui.dialogs.customer_data_suggestions.QMessageBox.warning") as warning:
            self.dialog._resolve(suggestion, False)
        warning.assert_called_once()
        self.repository.resolve_data_suggestion.side_effect = None
        self.repository.resolve_data_suggestion.return_value = Customer(id=None)
        self.dialog._resolve(suggestion, False)

    def test_contact_conflicts_cover_missing_matching_and_nonmatching_contacts(self):
        suggestion = CustomerDataSuggestion(
            suggestion_type="contact", contact_name="Max", contact_role="New",
            contact_email="new@example.test", contact_phone="222",
        )
        self.repository.get.return_value = None
        self.assertEqual(self.dialog._contact_conflict_text(suggestion), "")
        self.repository.get.return_value = Customer(
            id=7,
            contacts=[
                Contact(name="Other", email="other@example.test"),
                Contact(
                    name="Max", role="Old", email="old@example.test", phone="111"
                ),
            ],
        )
        conflict = self.dialog._contact_conflict_text(suggestion)
        self.assertIn("Rolle", conflict)
        self.dialog._suggestion_card(suggestion)
        same = CustomerDataSuggestion(
            suggestion_type="contact", contact_name="Max", contact_role="Old",
            contact_email="old@example.test", contact_phone="111",
        )
        self.assertEqual(self.dialog._contact_conflict_text(same), "")

    def test_scan_start_completion_error_and_reject_guards(self):
        running = Mock()
        running.isRunning.return_value = True
        self.dialog.scan_worker = running
        self.dialog._start_scan()
        running.start.assert_not_called()
        with patch("app.gui.dialogs.customer_data_suggestions.ContactScanWorker") as worker_class:
            worker = worker_class.return_value
            self.dialog.scan_worker = None
            self.dialog._start_scan()
        worker.start.assert_called_once_with()

        self.dialog._scan_finished(None, "broken")
        self.assertIn("broken", self.dialog.scan_result.text())
        changed = Mock()
        self.dialog.customerChanged.connect(changed)
        result = Mock(found_fields=3, applied_fields=2, pending_fields=1)
        self.dialog._scan_finished(result, "")
        changed.assert_called_with(7)
        self.assertIn("3 Felder", self.dialog.scan_result.text())

        self.dialog.scan_worker = running
        self.dialog.reject()
        self.assertTrue(self.dialog.isVisible() is False)
        self.dialog.scan_worker = None
        self.dialog.reject()
