from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from app.core.config import CustomerRecognitionOptions
from app.core.index_layout import IndexLayout
from app.core.search_models import SearchFilters, SearchPage
from app.gui.workers.contact_scan_worker import ContactScanWorker
from app.gui.workers.content_maintenance_worker import ContentMaintenanceWorker
from app.gui.workers.file_conversion_worker import FileConversionWorker
from app.gui.workers.search_worker import SearchWorker
from app.gui.workers.settings_data_worker import BlacklistCleanupWorker, SettingsDataWorker
from app.gui.workers.statistics_worker import StatisticsWorker
from tests.base.test_case import PapaGuiTestCase


class WorkerTests(PapaGuiTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def emissions(self, signal):
        values = []
        signal.connect(lambda *args: values.append(args))
        return values

    def test_contact_scan_worker_emits_success_and_error(self):
        worker = ContactScanWorker(Path("index"), Path("customers"), CustomerRecognitionOptions(), 7)
        emitted = self.emissions(worker.completed)
        service = Mock()
        service.rescan_customer_contacts.return_value = {"count": 1}
        with patch("app.gui.workers.contact_scan_worker.CustomerRecognitionService", return_value=service):
            worker.run()
        self.assertEqual(emitted[-1], ({"count": 1}, ""))
        with patch("app.gui.workers.contact_scan_worker.CustomerRecognitionService", side_effect=RuntimeError("bad")):
            worker.run()
        self.assertEqual(emitted[-1], (None, "bad"))

    def test_content_maintenance_worker_emits_success_and_error(self):
        worker = ContentMaintenanceWorker(IndexLayout(self.temp_path), "repair")
        emitted = self.emissions(worker.completed)
        service = Mock()
        service.repair.return_value = {"fixed": 1}
        with patch("app.gui.workers.content_maintenance_worker.ContentIndexMaintenance", return_value=service):
            worker.run()
        self.assertEqual(emitted[-1], ("repair", {"fixed": 1}, ""))
        with patch("app.gui.workers.content_maintenance_worker.ContentIndexMaintenance", side_effect=RuntimeError("bad")):
            worker.run()
        self.assertEqual(emitted[-1], ("repair", None, "bad"))

    def test_file_conversion_worker_runs_operations_errors_interrupts_and_cleanup(self):
        for operation, method, result in (
            (FileConversionWorker.XLS_TO_XLSX, "convert", Path("x.xlsx")),
            (FileConversionWorker.EXTRACT_DOC, "extract_legacy_doc", "text"),
            (FileConversionWorker.WORD_TO_PDF, "convert_word_to_pdf", Path("x.pdf")),
        ):
            worker = FileConversionWorker(3, operation, Path("source"))
            emitted = self.emissions(worker.completed)
            converter = Mock()
            getattr(converter, method).return_value = result
            converter.get_last_operation.return_value = {"tool": "fake"}
            with patch("app.gui.workers.file_conversion_worker.DocumentConverter", return_value=converter):
                worker.run()
            self.assertEqual(emitted[-1], (3, operation, result, {"tool": "fake"}, ""))
            self.assertIs(worker.take_converter(), converter)
            self.assertIsNone(worker.take_converter())

        worker = FileConversionWorker(1, "invalid", Path("source"))
        emitted = self.emissions(worker.completed)
        with patch("app.gui.workers.file_conversion_worker.DocumentConverter", return_value=Mock(get_last_operation=Mock(return_value={}))):
            worker.run()
        self.assertIn("Unbekannte", emitted[-1][-1])

        worker = FileConversionWorker(1, worker.EXTRACT_DOC, Path("source"))
        emitted = self.emissions(worker.completed)
        converter = Mock()
        converter.extract_legacy_doc.side_effect = InterruptedError
        with patch("app.gui.workers.file_conversion_worker.DocumentConverter", return_value=converter):
            worker.run()
        self.assertEqual(emitted[-1][-1], "abgebrochen")
        worker.cleanup()
        converter.cleanup.assert_called_once_with()

        worker = FileConversionWorker(1, worker.EXTRACT_DOC, Path("source"))
        emitted = self.emissions(worker.completed)
        converter = Mock()
        converter.extract_legacy_doc.return_value = "text"
        converter.get_last_operation.return_value = {}
        with (
            patch("app.gui.workers.file_conversion_worker.DocumentConverter", return_value=converter),
            patch.object(worker, "isInterruptionRequested", return_value=True),
        ):
            worker.run()
        self.assertEqual(emitted[-1][-1], "abgebrochen")

    def test_search_worker_routes_categories_and_reports_errors(self):
        filters = SearchFilters()
        worker = SearchWorker(Path("index"), 1, "customers", "q", 10, filters, 1, 5, Path("missing"))
        self.assertEqual(worker._search_customers(), SearchPage([], 0, 1, 5))

        repository = Mock()
        repository.search.return_value = [1, 2, 3]
        customer_path = self.make_file("customers.db")
        worker.customer_db_path = customer_path
        with patch("app.gui.workers.search_worker.CustomerRepository", return_value=repository):
            self.assertEqual(worker._search_customers().items, [1, 2, 3])
        repository.close.assert_called_once_with()

        manager = Mock()
        page = SearchPage([], 0, 1, 5)
        for category, method in (("folders", "search_folders_page"), ("files", "search_files_page"), ("text", "search_text_page")):
            worker.category = category
            getattr(manager, method).return_value = page
            self.assertIs(worker._search_index(manager), page)
        worker.category = "invalid"
        with self.assertRaises(ValueError):
            worker._search_index(manager)

        emitted = self.emissions(worker.completed)
        worker.category = "customers"
        with patch.object(worker, "_search_customers", return_value=page):
            worker.run()
        self.assertEqual(emitted[-1][-2:], (page, ""))
        with patch.object(worker, "_search_customers", side_effect=RuntimeError("bad")):
            worker.run()
        self.assertEqual(emitted[-1][-2:], ([], "bad"))

        worker.category = "text"
        worker.index_layout = IndexLayout(self.temp_path / "index")
        service = Mock()
        service.search_page.return_value = page
        with patch("app.gui.workers.search_worker.ContentSearchService", return_value=service):
            worker.run()
        self.assertIs(emitted[-1][2], page)

    def test_settings_data_worker_loads_payload_closes_repository_and_reports_error(self):
        worker = SettingsDataWorker(Path("index"), Path("customers"))
        emitted = self.emissions(worker.completed)
        repository = Mock()
        repository.last_recognition_run.return_value = {"done": True}
        repository.pending_recognition_count.return_value = 2
        repository.list_blacklist_suggestions.return_value = ["x"]
        with (
            patch("app.gui.workers.settings_data_worker.available_backups", return_value=[Path("backup")]),
            patch("app.gui.workers.settings_data_worker.IndexDiagnosticsService"),
            patch("app.gui.workers.settings_data_worker.StatisticsService"),
            patch("app.gui.workers.settings_data_worker.CustomerRepository", return_value=repository),
        ):
            worker.run()
        self.assertEqual(emitted[-1][0]["pending_recognition_cases"], 2)
        repository.close.assert_called_once_with()

        layout = IndexLayout(self.temp_path / "layout")
        worker = SettingsDataWorker(Path("index"), Path("customers"), layout)
        emitted = self.emissions(worker.completed)
        with patch("app.gui.workers.settings_data_worker.CatalogStore", side_effect=RuntimeError("bad")):
            worker.run()
        self.assertEqual(emitted[-1][0]["error"], "bad")

    def test_blacklist_and_statistics_workers_emit_success_and_errors(self):
        blacklist = BlacklistCleanupWorker(Path("customers"), object())
        blacklist_emitted = self.emissions(blacklist.completed)
        repository = Mock()
        repository.cleanup_automatic_blacklisted_values.return_value = {"removed": 2}
        with patch("app.gui.workers.settings_data_worker.CustomerRepository", return_value=repository):
            blacklist.run()
        self.assertEqual(blacklist_emitted[-1], ({"removed": 2}, ""))
        repository.close.assert_called_once_with()
        with patch("app.gui.workers.settings_data_worker.CustomerRepository", side_effect=RuntimeError("bad")):
            blacklist.run()
        self.assertEqual(blacklist_emitted[-1], ({}, "bad"))

        statistics = StatisticsWorker(Path("index"), Path("customers"), Path("state"))
        statistics_emitted = self.emissions(statistics.completed)
        service = Mock()
        service.load.return_value = {"files": 1}
        with patch("app.gui.workers.statistics_worker.StatisticsService", return_value=service):
            statistics.run()
        self.assertEqual(statistics_emitted[-1], ({"files": 1}, ""))
        with patch("app.gui.workers.statistics_worker.StatisticsService", side_effect=RuntimeError("bad")):
            statistics.run()
        self.assertEqual(statistics_emitted[-1], (None, "bad"))
