from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.config import CustomerRecognitionOptions
from app.core.customer_models import Customer
from app.core.customer_recognition_models import ExtractionEvidence, RecognitionCandidate
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs.customer_recognition_review import CustomerRecognitionReviewDialog
from app.services.customer_recognition import CustomerRecognitionService


class CustomerRecognitionReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_empty_invalid_selection_evidence_and_resolution_errors(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "customers.db"
            repository = CustomerRepository(database)
            customer = repository.save(Customer(display_name="Ohne Kontext"))
            repository.close()
            dialog = CustomerRecognitionReviewDialog(
                Path(directory) / "index.db",
                database,
                CustomerRecognitionOptions(enabled=True),
            )
            self.assertIn("Keine offenen", dialog.case_title.text())
            self.assertEqual(dialog.customer_list.count(), 1)
            self.assertFalse(dialog._select_customer(99999))
            fake_repository = SimpleNamespace(
                list_customers=lambda: [Customer(display_name="Noch nicht gespeichert")],
                close=lambda: None,
            )
            with patch.object(dialog, "_repository", return_value=fake_repository):
                dialog._load_customers()
            self.assertEqual(dialog._customers, [])
            dialog._show_selected_case(None)
            dialog._resolve("ignore")

            candidate = RecognitionCandidate(
                "key", "Test", "", ["/one", "/two"], [], [],
                suggested_customer_ids=[99999],
                evidence=[ExtractionEvidence(
                    "name", "Test", "test", "/one/source.txt", "context", 0,
                    "rule", 0.8, True,
                )],
            )
            dialog._cases = {candidate.signature: candidate}
            dialog.case_list.clear()
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem("Test")
            item.setData(Qt.UserRole, candidate.signature)
            dialog.case_list.addItem(item)
            dialog.case_list.setCurrentItem(item)
            dialog._show_selected_case(item)
            self.assertIn("Erkennungsbelege", dialog.case_details.toPlainText())
            self.assertTrue(dialog.separate_button.isEnabled())

            dialog.customer_list.clearSelection()
            dialog.customer_list.setCurrentItem(None)
            with patch.object(QMessageBox, "warning") as warning:
                dialog._resolve("assign")
            warning.assert_called_once()

            dialog._select_customer(int(customer.id))
            with patch.object(
                CustomerRecognitionService, "resolve_case", side_effect=RuntimeError("bad")
            ), patch.object(QMessageBox, "warning") as warning:
                dialog._customers = [(int(customer.id), "Ohne Kontext", "ohne kontext")]
                dialog._filter_customers()
                dialog._select_customer(int(customer.id))
                dialog._resolve("assign")
            self.assertIn("bad", warning.call_args.args[-1])
            dialog.close()


if __name__ == "__main__":
    unittest.main()
