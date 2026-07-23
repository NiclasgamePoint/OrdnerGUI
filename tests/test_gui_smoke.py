from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.core.config import CustomerRecognitionOptions
from app.core.customer_models import Customer
from app.core.customer_recognition_models import (
    ContactScanStats,
    RecognitionCandidate,
)
from app.core.customer_repository import CustomerRepository
from app.core.index_manager import IndexManager
from app.gui.dialogs.customer_recognition_review import CustomerRecognitionReviewDialog
from app.gui.dialogs.customer_data_suggestions import CustomerDataSuggestionsDialog
from app.gui.main_window import MainWindow
from app.gui.settings_popup import SettingsPopup
from app.services.customer_recognition import CustomerRecognitionService


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_smoke_without_auto_index(self):
        with patch.object(MainWindow, "_initialize_data_source", lambda self: None):
            window = MainWindow()
            self.app.processEvents()
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
            long_name = (
                "Sehr langer vollständiger Kundenname für eine gut lesbare "
                "mehrzeilige Darstellung"
            )
            repository.replace_pending_recognition_cases([
                RecognitionCandidate(
                    recognition_key="langer kunde",
                    display_name=long_name,
                    city="Ort mit langem Namen",
                    folder_paths=["/tmp/vollstaendiger/projektpfad"],
                    service_types=["Beratung"],
                    years=[2015],
                    reason="Manuelle Prüfung",
                )
            ])
            repository.close()

            dialog = CustomerRecognitionReviewDialog(
                index_path=index_path,
                customer_database_path=customers_path,
                options=CustomerRecognitionOptions(enabled=True),
            )
            dialog.show()
            self.app.processEvents()
            self.assertIsNotNone(dialog.case_list)
            self.assertIsNotNone(dialog.customer_search)
            self.assertIsNotNone(dialog.customer_list)
            self.assertGreaterEqual(dialog.case_list.minimumWidth(), 360)
            self.assertTrue(dialog.case_list.wordWrap())
            self.assertEqual(dialog.case_list.textElideMode(), Qt.ElideNone)
            self.assertEqual(
                dialog.case_list.horizontalScrollBarPolicy(),
                Qt.ScrollBarAlwaysOff,
            )
            self.assertEqual(dialog.case_list.item(0).text().split(" · ", 1)[0], long_name)
            self.assertGreaterEqual(dialog.splitter.sizes()[0], 360)
            dialog.close()

    def test_customer_recognition_review_customer_search_and_selection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "index.db"
            customers_path = root / "customers.db"
            with IndexManager(index_path) as _manager:
                pass
            repository = CustomerRepository(customers_path)
            first = repository.save(Customer(
                display_name="Muster GmbH",
                entity_type="Unternehmen",
                city="Hamburg",
            ))
            repository.save(Customer(
                display_name="Erika Beispiel",
                entity_type="Privatperson",
                city="Berlin",
            ))
            candidate = RecognitionCandidate(
                recognition_key="muster",
                display_name="Muster",
                city="Hamburg",
                folder_paths=["/tmp/projekt"],
                service_types=["Beratung"],
                years=[2026],
                reason="Ähnlicher Name",
                suggested_customer_ids=[int(first.id)],
            )
            repository.replace_pending_recognition_cases([candidate])
            repository.close()

            dialog = CustomerRecognitionReviewDialog(
                index_path=index_path,
                customer_database_path=customers_path,
                options=CustomerRecognitionOptions(enabled=True),
            )
            self.assertEqual(dialog.customer_list.count(), 2)
            self.assertEqual(dialog._selected_customer_id(), int(first.id))

            dialog.customer_search.setText("berlin")
            self.assertEqual(dialog.customer_list.count(), 1)
            self.assertIn("Erika Beispiel", dialog.customer_list.item(0).text())
            dialog.customer_search.setText("unternehmen")
            self.assertEqual(dialog.customer_list.count(), 1)
            self.assertIn("Muster GmbH", dialog.customer_list.item(0).text())
            dialog.customer_search.setText("nicht vorhanden")
            self.assertEqual(dialog.customer_list.count(), 1)
            self.assertEqual(
                dialog.customer_list.item(0).text(),
                "Keine passenden Kunden gefunden.",
            )
            self.assertIsNone(dialog._selected_customer_id())

            dialog.customer_search.clear()
            dialog.customer_list.setCurrentRow(1)
            selected_customer_id = dialog._selected_customer_id()
            with patch.object(
                CustomerRecognitionService,
                "resolve_case",
                return_value=[],
            ) as resolve_case:
                dialog._resolve("assign")
            self.assertEqual(resolve_case.call_args.args[-1], selected_customer_id)
            dialog.close()

    def test_customer_contact_scan_dialog_shows_button_and_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "index.db"
            customers_path = root / "customers.db"
            with IndexManager(index_path) as _manager:
                pass
            repository = CustomerRepository(customers_path)
            customer = repository.save(Customer(display_name="Muster"))
            dialog = CustomerDataSuggestionsDialog(
                repository,
                int(customer.id),
                index_path,
                CustomerRecognitionOptions(enabled=True),
            )

            self.assertEqual(dialog.scan_button.text(), "Kontaktdaten neu suchen")
            self.assertEqual(dialog.scan_button.property("buttonRole"), "secondary")
            self.assertEqual(dialog.close_button.property("buttonRole"), "primary")
            dialog._scan_finished(ContactScanStats(
                scanned_projects=1,
                found_fields=3,
                applied_fields=1,
                pending_fields=2,
            ), "")
            self.assertIn("3 Felder gefunden", dialog.scan_result.text())
            self.assertIn("2 jetzt zu prüfen", dialog.scan_result.text())

            dialog.close()
            repository.close()

    def test_settings_popup_smoke(self):
        popup = SettingsPopup("light", "#2db89d")
        self.assertEqual(popup.nav_list.count(), 4)
        popup.close()


if __name__ == "__main__":
    unittest.main()
