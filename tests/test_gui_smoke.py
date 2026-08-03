from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox

from app.core.config import CustomerRecognitionOptions, forced_fullscreen
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

    def test_forced_fullscreen_is_enabled_only_when_vnc_flag_is_set(self):
        with patch.dict("os.environ", {"PAPAGUI_FORCE_FULLSCREEN": "1"}, clear=False):
            self.assertTrue(forced_fullscreen())
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(forced_fullscreen())

    def test_accepted_data_path_is_saved_before_index_build_finishes(self):
        with TemporaryDirectory() as directory:
            selected = Path(directory).resolve()
            with (
                patch.object(MainWindow, "_initialize_data_source", lambda self: None),
                patch(
                    "app.gui.main_window.IndexJobController.adopt_running_job",
                    return_value=False,
                ),
            ):
                window = MainWindow()
            window.index_controller.is_active = lambda: False

            with (
                patch("app.gui.main_window.save_index_source") as save_source,
                patch.object(window, "_start_background_indexing") as start_index,
            ):
                window.on_settings_data_path_changed(str(selected))

            save_source.assert_called_once_with(selected)
            self.assertEqual(window.index_source, selected)
            self.assertEqual(window.pending_index_source, selected)
            start_index.assert_called_once()
            window.close()

    def test_explicit_data_path_is_not_overwritten_by_stale_completed_job(self):
        with TemporaryDirectory() as directory:
            selected = Path(directory).resolve()
            with (
                patch.object(MainWindow, "_initialize_data_source", lambda self: None),
                patch(
                    "app.gui.main_window.get_configured_index_source",
                    return_value=selected,
                ),
                patch(
                    "app.gui.main_window.has_configured_index_source",
                    return_value=True,
                ),
                patch(
                    "app.gui.main_window.IndexJobController.adopt_running_job",
                    return_value=False,
                ),
                patch("app.gui.main_window.save_index_source") as save_source,
            ):
                window = MainWindow()

            self.assertEqual(window.index_source, selected)
            save_source.assert_not_called()
            window.close()

    def test_main_window_opens_existing_file_with_standard_program(self):
        with TemporaryDirectory() as directory:
            document = Path(directory) / "tabelle.xlsx"
            document.write_bytes(b"workbook")
            with patch.object(MainWindow, "_initialize_data_source", lambda self: None):
                window = MainWindow()
            with (
                patch(
                    "app.gui.main_window.QDesktopServices.openUrl",
                    return_value=True,
                ) as open_url,
                patch("app.gui.main_window.QMessageBox.warning") as warning,
            ):
                window.open_native_file(str(document))

            opened_url = open_url.call_args.args[0]
            self.assertEqual(Path(opened_url.toLocalFile()), document.resolve())
            warning.assert_not_called()
            window.close()

    def test_main_window_reports_missing_and_failed_external_file_open(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "fehlt.docx"
            existing = root / "vorhanden.docx"
            existing.write_bytes(b"document")
            with patch.object(MainWindow, "_initialize_data_source", lambda self: None):
                window = MainWindow()
            with (
                patch(
                    "app.gui.main_window.QDesktopServices.openUrl",
                    return_value=False,
                ) as open_url,
                patch("app.gui.main_window.QMessageBox.warning") as warning,
            ):
                window.open_native_file(str(missing))
                open_url.assert_not_called()
                self.assertEqual(warning.call_args.args[1], "Datei nicht gefunden")

                window.open_native_file(str(existing))
                open_url.assert_called_once()
                self.assertEqual(
                    warning.call_args.args[1],
                    "Datei konnte nicht geöffnet werden",
                )
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
            dialog.entity_type_combo.setCurrentIndex(
                dialog.entity_type_combo.findData("Unternehmen")
            )
            selected_customer_id = dialog._selected_customer_id()
            with patch.object(
                CustomerRecognitionService,
                "resolve_case",
                return_value=[],
            ) as resolve_case:
                dialog._resolve("assign")
            self.assertEqual(resolve_case.call_args.args[-1], selected_customer_id)
            self.assertEqual(
                resolve_case.call_args.args[0].entity_type,
                "Unternehmen",
            )
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

    def test_customer_type_can_be_changed_in_contact_review(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "index.db"
            customers_path = root / "customers.db"
            with IndexManager(index_path) as _manager:
                pass
            repository = CustomerRepository(customers_path)
            customer = repository.save(Customer(
                display_name="Unklarer Kunde",
                entity_type="Privatperson",
            ))
            suggestion = repository.apply_project_suggestion(
                int(customer.id),
                None,
                "entity_type",
                "Privatperson",
                rule="Kundentyp unsicher",
                confidence=0.55,
            )
            dialog = CustomerDataSuggestionsDialog(
                repository,
                int(customer.id),
                index_path,
                CustomerRecognitionOptions(enabled=True),
            )

            type_combos = [
                combo for combo in dialog.findChildren(QComboBox)
                if combo.accessibleName() == "Kundentyp auswählen"
            ]
            self.assertEqual(len(type_combos), 1)
            type_combos[0].setCurrentIndex(
                type_combos[0].findData("Unternehmen")
            )
            dialog._resolve(suggestion, True, "Unternehmen")

            self.assertEqual(
                repository.get(int(customer.id)).entity_type,
                "Unternehmen",
            )
            self.assertEqual(repository.list_data_suggestions(int(customer.id)), [])
            dialog.close()
            repository.close()

    def test_settings_popup_smoke(self):
        popup = SettingsPopup("light", "#2db89d")
        self.assertEqual(popup.nav_list.count(), 5)
        self.assertIsNotNone(popup.statistics_widget)
        popup.close()


if __name__ == "__main__":
    unittest.main()
