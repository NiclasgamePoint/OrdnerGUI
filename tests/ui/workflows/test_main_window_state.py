from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QMoveEvent, QResizeEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon

from app.core.customer_models import Customer
from app.core.config import CustomerRecognitionOptions
from app.core.search_models import SearchCoverage, SearchPage, SearchSort
from app.gui.main_window import MainWindow
from app.gui.navigation import NavigationEntry
from tests.base.qt_test_case import QtTestCase

ORIGINAL_INIT_SYSTEM_TRAY = MainWindow._init_system_tray
ORIGINAL_OPEN_ACTIVE_INDEX = MainWindow._open_active_index


class MainWindowStateTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.manager = Mock(db_path=self.temp_path / "index.db")
        self.manager.get_metadata.return_value = ""
        self.repository = Mock()
        self.repository.search.return_value = []
        self.repository.get.return_value = None
        self.patches = [
            patch.object(MainWindow, "_init_system_tray"),
            patch.object(MainWindow, "_open_active_index", return_value=self.manager),
            patch("app.gui.main_window.CustomerRepository", return_value=self.repository),
        ]
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        with (
            patch.object(MainWindow, "_initialize_data_source"),
            patch.object(MainWindow, "_reconcile_source_from_completed_job"),
            patch.object(MainWindow, "_refresh_search_facets"),
            patch.object(MainWindow, "_start_content_indexing_if_enabled"),
            patch.object(MainWindow, "_refresh_statistics"),
        ):
            self.window = MainWindow()
        self.window.folder_page.cleanup = Mock()
        self.addCleanup(self.window.deleteLater)

    def tearDown(self):
        self.window.settings_popup = None
        super().tearDown()

    def test_content_callbacks_cover_pending_actions_completion_and_errors(self):
        popup = Mock()
        self.window.settings_popup = popup
        self.window._on_content_progress({"status": "idle"})
        self.window._on_content_progress({"status": "running", "completed_documents": 1, "total_documents": 4})
        self.assertIn("25 %", self.window.status_bar.status_label.text())
        self.window._on_content_progress({"status": "running", "total_documents": 0})
        self.assertIn("0 %", self.window.status_bar.status_label.text())

        self.window.pending_content_maintenance = "repair"
        with patch.object(self.window, "_start_content_maintenance") as maintenance:
            self.window._on_content_finished({"status": "cancelled"})
            QApplication.processEvents()
        maintenance.assert_called_once_with("repair")
        self.window.pending_content_restart = True
        with patch.object(self.window, "_start_content_indexing_if_enabled") as restart:
            self.window._on_content_finished({"status": "cancelled"})
        restart.assert_called_once_with()
        with (
            patch.object(self.window, "_refresh_statistics") as statistics,
            patch.object(self.window, "_refresh_customer_results_only") as customers,
        ):
            self.window._on_content_finished({"status": "completed", "failed_documents": 2})
        self.assertIn("2 Fehler", self.window.status_bar.status_label.text())
        statistics.assert_called_once_with()
        customers.assert_called_once_with()
        self.window._on_content_finished({"status": "error", "error": "broken"})
        self.assertIn("broken", self.window.status_bar.status_label.text())

        self.window.index_options = SimpleNamespace(content_indexing_enabled=False)
        self.assertFalse(self.window._start_content_indexing_if_enabled())
        self.window.index_options = SimpleNamespace(content_indexing_enabled=True)
        self.window.content_job_controller.start_or_adopt = Mock(return_value=True)
        self.assertTrue(self.window._start_content_indexing_if_enabled())

    def test_initialization_reconnect_and_completed_job_reconciliation(self):
        with (
            patch("app.gui.main_window.has_configured_index_source", return_value=False),
            patch("app.gui.main_window.OnboardingDialog") as dialog_class,
        ):
            dialog_class.return_value.exec.return_value = QDialog.Rejected
            dialog_class.return_value.selected_path = None
            self.window._initialize_data_source()
        self.assertIn("keine Datenquelle", self.window.status_bar.status_label.text())
        selected = self.temp_path / "source"
        selected.mkdir()
        with (
            patch("app.gui.main_window.has_configured_index_source", return_value=False),
            patch("app.gui.main_window.OnboardingDialog") as dialog_class,
            patch("app.gui.main_window.save_index_source") as save,
            patch.object(self.window, "check_and_index") as check,
            patch.object(self.window, "_start_filesystem_monitor") as monitor,
        ):
            dialog_class.return_value.exec.return_value = QDialog.Accepted
            dialog_class.return_value.selected_path = selected
            self.window._initialize_data_source()
        save.assert_called_once_with(selected)
        check.assert_called_once_with()
        monitor.assert_called_once_with()

        self.window.index_source = self.temp_path / "missing"
        with patch.object(self.window, "check_and_index") as check:
            self.window._try_reconnect_source()
        check.assert_not_called()
        self.window.index_source = selected
        with (
            patch.object(self.window, "check_and_index") as check,
            patch.object(self.window, "_start_filesystem_monitor") as monitor,
        ):
            self.window._try_reconnect_source()
        check.assert_called_once_with()
        monitor.assert_called_once_with()

        with patch("app.gui.main_window.has_configured_index_source", return_value=True):
            self.window._reconcile_source_from_completed_job()
        self.window.index_controller.current_state = Mock(return_value={
            "status": "completed", "activated_by": "worker", "source": str(selected),
        })
        self.manager.get_metadata.return_value = str(selected)
        with (
            patch("app.gui.main_window.has_configured_index_source", return_value=False),
            patch("app.gui.main_window.save_index_source") as save,
        ):
            self.window._reconcile_source_from_completed_job()
        save.assert_called_once_with(selected)

    def test_routes_customer_folder_success_missing_and_error(self):
        self.window._show_route(NavigationEntry("search"))
        self.assertIs(self.window.page_stack.currentWidget(), self.window.search_page)
        self.repository.get.return_value = None
        with patch("app.gui.main_window.QTimer.singleShot") as later:
            self.window._show_route(NavigationEntry("customer", 4))
        later.assert_called_once()
        customer = Customer(id=4, display_name="Customer", folder_path="/one", folder_paths=["/one", "/two"])
        self.repository.get.return_value = customer
        self.manager.get_folder_summary.side_effect = lambda path: {"path": path}
        self.window.customer_page.set_customer = Mock()
        self.window._show_route(NavigationEntry("customer", 4))
        self.assertEqual(self.window.customer_page.set_customer.call_args.args[1]["/two"], {"path": "/two"})

        details = {"folder_name": "Folder", "file_count": 3}
        self.manager.get_folder_details.return_value = details
        self.window.folder_page.set_folder = Mock()
        self.window._show_route(NavigationEntry("folder", "/folder"))
        self.window.folder_page.set_folder.assert_called_once_with(details)
        with (
            patch.object(self.manager, "get_folder_details", side_effect=RuntimeError("bad")),
            patch("app.gui.main_window.QMessageBox.warning") as warning,
            patch("app.gui.main_window.QTimer.singleShot") as later,
        ):
            self.window._show_route(NavigationEntry("folder", "/folder"))
        warning.assert_called_once()
        later.assert_called_once()

    def test_search_input_results_status_filters_and_facets(self):
        self.window.recent_customer_history.ids = Mock(return_value=[1, 2])
        self.repository.get.side_effect = [Customer(id=1, display_name="One"), None]
        self.window._show_initial_customers()
        self.assertIn("1 zuletzt", self.window.status_bar.status_label.text())
        self.window.recent_customer_history.ids.return_value = []
        self.window._show_initial_customers()
        self.assertIn("Suchbegriff", self.window.status_bar.status_label.text())

        with patch.object(self.window, "_cancel_outdated_searches") as cancel:
            self.window.on_search_text_changed("")
            self.window.on_search_text_changed("a")
            self.window.on_search_text_changed("ab")
        self.assertEqual(cancel.call_count, 3)
        self.assertTrue(self.window.search_debounce.isActive())
        self.window.search_debounce.stop()
        self.window.header.set_query("ab")
        with patch.object(self.window, "_launch_visible_searches") as launch:
            self.window._start_live_search()
        launch.assert_called_once()
        self.window.header.set_query("a")
        with patch.object(self.window, "_launch_visible_searches") as launch:
            self.window._start_live_search()
            self.window.start_full_search()
        launch.assert_not_called()

        self.window.search_generation = 3
        with patch.object(self.window, "_update_search_status") as update:
            self.window._on_search_completed(2, "folders", [], "")
        update.assert_not_called()
        for category, setter in (
            ("customers", "set_customer_error"),
            ("folders", "set_folder_error"),
            ("text", "set_document_error"),
        ):
            setattr(self.window.search_page, setter, Mock())
            self.window._on_search_completed(3, category, [], "bad")
            getattr(self.window.search_page, setter).assert_called_once_with("bad")
        coverage = SearchCoverage(1, 4, 1, 4, False)
        self.window.index_options = SimpleNamespace(
            content_search_enabled=True,
            result_limit=10,
            maximum_parallel_shards=2,
        )
        for category in ("customers", "folders", "text"):
            items = (
                [Customer(id=1, display_name="One")]
                if category == "customers"
                else [{"folder_name": "Folder", "path": "/file"}]
            )
            page = SearchPage(items, 1, 1, 10, coverage=coverage)
            self.window._on_search_completed(3, category, page, "")
        self.assertIn("Inhaltsindex: 25 %", self.window.status_bar.status_label.text())
        self.window.content_search_coverage = SearchCoverage(1, 0, 0, 0, False)
        self.assertEqual(self.window._content_coverage_status(), "")
        self.window.content_search_coverage = SearchCoverage(1, 1, 1, 1, True)
        self.assertEqual(self.window._content_coverage_status(), "")

        worker = Mock()
        self.window.search_workers = {worker}
        self.window._cancel_outdated_searches()
        worker.requestInterruption.assert_called_once_with()
        self.window._release_search_worker(worker)
        worker.deleteLater.assert_called_once_with()

        self.manager.get_search_facets.return_value = {
            "domains": ["A"], "years": [2026], "file_types": ["pdf"],
        }
        self.window._refresh_search_facets()
        self.assertEqual(self.window.domain_filter.itemData(1), "A")
        with patch.object(self.manager, "get_search_facets", side_effect=RuntimeError):
            self.window._refresh_search_facets()

        self.window.filter_popup.sort_combo.setCurrentIndex(
            self.window.filter_popup.sort_combo.findData(SearchSort.DATE)
        )
        self.window.header.set_query("ab")
        with patch.object(self.window, "start_full_search") as search:
            self.window._on_filter_changed()
        search.assert_called_once_with()

    def test_tray_navigation_native_paths_and_customer_management(self):
        self.window.tray_icon = None
        self.window._update_system_tray()
        tray = Mock()
        self.window.tray_icon = tray
        self.window.status_bar.set_text("")
        self.window._update_system_tray()
        self.assertIn("Bereit", tray.setToolTip.call_args.args[0])
        with patch.object(self.window, "_show_from_tray") as show:
            self.window._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        show.assert_called_once_with()
        with patch.object(self.window.navigator, "navigate") as navigate:
            self.window.open_customer_page(4)
            self.window.open_folder_page("")
            self.window.open_folder_page("/folder")
        self.assertEqual(navigate.call_count, 2)

        missing = self.temp_path / "missing"
        with patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window.open_native_path(str(missing))
            self.window.open_native_file(str(missing))
        self.assertEqual(warning.call_count, 2)
        folder = self.temp_path / "folder"
        folder.mkdir()
        file_path = folder / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        with (
            patch("app.gui.main_window.QDesktopServices.openUrl", return_value=False),
            patch("app.gui.main_window.QMessageBox.warning") as warning,
        ):
            self.window.open_native_path(str(file_path))
            self.window.open_native_file(str(file_path))
        self.assertEqual(warning.call_count, 2)

        dialog = Mock()
        dialog.exec.return_value = False
        with patch("app.gui.main_window.CustomerEditorDialog", return_value=dialog):
            self.window.manage_folder_customer("/folder", "Folder")
        self.repository.get_by_folder.return_value = SimpleNamespace(id=7)
        dialog.exec.return_value = True
        with (
            patch("app.gui.main_window.CustomerEditorDialog", return_value=dialog),
            patch.object(self.window, "_refresh_customer_results_only") as refresh,
            patch.object(self.window.navigator, "navigate") as navigate,
        ):
            self.window.manage_folder_customer("/folder", "Folder")
        refresh.assert_called_once_with()
        navigate.assert_called_once_with("customer", 7)

    def test_content_maintenance_questions_waiting_and_completion(self):
        popup = Mock()
        popup.run_modal_preserving_popup.side_effect = lambda operation: operation()
        self.window.settings_popup = popup
        with patch("app.gui.main_window.QMessageBox.question", return_value=1) as question:
            self.assertEqual(self.window._settings_question("Title", "Text"), 1)
        question.assert_called_once()
        with patch("app.gui.main_window.QMessageBox.warning", return_value=2):
            self.assertEqual(self.window._settings_warning("Title", "Text"), 2)
        self.window.settings_popup = None
        with patch("app.gui.main_window.QMessageBox.question", return_value=3):
            self.assertEqual(self.window._settings_question("Title", "Text"), 3)

        busy_worker = Mock()
        busy_worker.isRunning.return_value = True
        self.window.content_maintenance_worker = busy_worker
        self.window._start_content_maintenance("optimize")
        self.assertIn("bereits", self.window.status_bar.status_label.text())
        self.window.content_maintenance_worker = None
        self.window.content_job_controller.is_active = Mock(return_value=True)
        self.window.content_job_controller.pause = Mock()
        self.window.content_job_controller.cancel = Mock()
        self.window._start_content_maintenance("clear")
        self.window.content_job_controller.pause.assert_called_once_with()
        self.window.content_job_controller.is_active.return_value = True
        self.window._start_content_maintenance("rebuild")
        self.window.content_job_controller.cancel.assert_called_once_with()

        self.window.content_job_controller.is_active.return_value = False
        worker = Mock()
        with (
            patch("app.gui.main_window.ContentMaintenanceWorker", return_value=worker),
            patch.object(self.window, "_wait_for_content_index_readers") as wait,
        ):
            self.window._start_content_maintenance("retry_failed")
        wait.assert_called_once_with()
        worker.start.assert_called_once_with()

        search_worker = Mock()
        settings_worker = Mock()
        statistics_worker = Mock()
        settings_worker.isRunning.return_value = True
        statistics_worker.isRunning.return_value = True
        self.window.search_workers = {search_worker}
        self.window.settings_data_worker = settings_worker
        self.window.statistics_worker = statistics_worker
        self.window._wait_for_content_index_readers()
        search_worker.wait.assert_called_once_with()
        settings_worker.requestInterruption.assert_called_once_with()
        statistics_worker.requestInterruption.assert_called_once_with()

        self.window.settings_popup = popup
        with patch.object(self.window, "_settings_warning") as warning:
            self.window._on_content_maintenance_complete("optimize", None, "bad")
        warning.assert_called_once()
        self.window._on_content_maintenance_complete("clear", {}, "")
        popup.set_content_index_state.assert_called_with({})
        with patch.object(self.window, "_start_content_indexing_if_enabled") as restart:
            self.window._on_content_maintenance_complete("optimize", {}, "")
        restart.assert_called_once_with()

    def test_settings_data_statistics_loading_and_payload(self):
        self.window.settings_popup = None
        self.window._refresh_settings_popup_data()
        popup = Mock()
        self.window.settings_popup = popup
        with patch.object(self.window, "_start_settings_data_load") as load:
            self.window._refresh_settings_popup_data()
        load.assert_called_once_with()

        old_worker = Mock()
        old_worker.isRunning.return_value = True
        self.window.settings_data_worker = old_worker
        new_worker = Mock()
        with patch("app.gui.main_window.SettingsDataWorker", return_value=new_worker):
            self.window._start_settings_data_load()
        old_worker.requestInterruption.assert_called_once_with()
        old_worker.wait.assert_called_once_with()
        new_worker.start.assert_called_once_with()

        running = Mock()
        running.isRunning.return_value = True
        self.window.statistics_worker = running
        self.window._refresh_statistics()
        self.assertTrue(self.window.statistics_refresh_pending)
        self.window.statistics_worker = None
        statistics = Mock()
        with patch("app.gui.main_window.StatisticsWorker", return_value=statistics):
            self.window._refresh_statistics()
        statistics.start.assert_called_once_with()

        payload = {
            "backups": ["backup"], "diagnostics": "ok",
            "recognition_summary": {"count": 1}, "pending_recognition_cases": 2,
            "blacklist_suggestions": ["x"], "statistics": "stats", "error": "bad",
        }
        self.window._on_settings_data_loaded(payload)
        popup.set_backups.assert_called_once_with(["backup"])
        self.assertIn("bad", self.window.status_bar.status_label.text())
        self.window.settings_popup = None
        self.window._on_settings_data_loaded(payload)

    def test_customer_data_clear_recognition_and_blacklists(self):
        self.window.index_controller.is_active = Mock(return_value=True)
        with patch("app.gui.main_window.QMessageBox.information") as information:
            self.window.confirm_clear_customer_data()
        information.assert_called_once()
        self.window.index_controller.is_active.return_value = False
        with patch("app.gui.main_window.QMessageBox.question", return_value=0), patch.object(self.window, "clear_customer_data") as clear:
            self.window.confirm_clear_customer_data()
        clear.assert_not_called()
        with patch("app.gui.main_window.QMessageBox.question", return_value=16384), patch.object(self.window, "clear_customer_data") as clear:
            self.window.confirm_clear_customer_data()
        clear.assert_called_once_with()

        replacement = Mock()
        self.window.settings_popup = Mock()
        with (
            patch("app.gui.main_window.CustomerRepository", return_value=replacement),
            patch.object(self.window, "_show_initial_customers"),
            patch.object(self.window, "_refresh_statistics"),
        ):
            self.window.clear_customer_data()
        self.assertIs(self.window.customer_repository, replacement)
        replacement.clear_all_customer_data.side_effect = RuntimeError("bad")
        with (
            patch("app.gui.main_window.CustomerRepository", return_value=Mock()),
            patch("app.gui.main_window.QMessageBox.warning") as warning,
        ):
            self.window.clear_customer_data()
        warning.assert_called_once()

        options = CustomerRecognitionOptions(
            email_blacklist="", phone_blacklist="", name_blacklist="", address_blacklist=""
        )
        with (
            patch("app.gui.main_window.save_customer_recognition_options") as save,
            patch("app.gui.main_window.QTimer.singleShot") as later,
        ):
            self.window.on_customer_recognition_options_changed(options)
        save.assert_called_once_with(options)
        later.assert_called_once()

        cleanup_worker = Mock()
        cleanup_worker.isRunning.return_value = True
        self.window.blacklist_cleanup_worker = cleanup_worker
        self.window._cleanup_blacklisted_values()
        cleanup_worker.start.assert_not_called()
        self.window.blacklist_cleanup_worker = None
        new_worker = Mock()
        with patch("app.gui.main_window.BlacklistCleanupWorker", return_value=new_worker):
            self.window._cleanup_blacklisted_values()
        new_worker.start.assert_called_once_with()
        with patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window._on_blacklist_cleanup_complete({}, "bad")
        warning.assert_called_once()
        with patch.object(self.window, "_refresh_customer_results_only") as refresh:
            self.window._on_blacklist_cleanup_complete({"fields": 1, "contacts": 2}, "")
        refresh.assert_called_once_with()

        self.window.recognition_options = options
        self.window.customer_repository = Mock()
        self.window.settings_popup = None
        with (
            patch.object(self.window, "_cleanup_blacklisted_values") as cleanup,
            patch("app.gui.main_window.save_customer_recognition_options"),
        ):
            self.window.confirm_blacklist_suggestion(1, "unknown", "x")
            self.window.confirm_blacklist_suggestion(1, "email", "x@example.test")
            self.window.confirm_blacklist_suggestion(1, "email", "X@example.test")
        self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(options.email_blacklist, "x@example.test")
        self.window.dismiss_blacklist_suggestion(1)
        self.window.customer_repository.set_blacklist_suggestion_status.assert_called_with(1, "dismissed")

    def test_appearance_and_data_source_changes(self):
        manager = self.window.theme_manager
        with patch.object(self.window, "apply_theme") as apply:
            self.window.on_settings_appearance_changed("dark", "#fff", 2, 14)
        self.assertEqual((manager.mode, manager.accent, manager.contrast, manager.font_size), ("dark", "#ffffff", 70, 14))
        apply.assert_called_once_with()
        with patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window.on_settings_data_path_changed(str(self.temp_path / "missing"))
        warning.assert_called_once()
        source = self.temp_path / "source"
        source.mkdir()
        self.window.index_controller.is_active = Mock(return_value=True)
        with patch("app.gui.main_window.QMessageBox.information") as information:
            self.window.on_settings_data_path_changed(str(source))
        information.assert_called_once()
        self.window.index_controller.is_active.return_value = False
        self.window.index_source = source
        self.manager.has_index_for_root.return_value = True
        with (
            patch("app.gui.main_window.save_index_source") as save,
            patch.object(self.window, "_reset_views_for_source_change") as reset,
        ):
            self.window.on_settings_data_path_changed(str(source))
        save.assert_called_once_with(source)
        reset.assert_called_once_with()
        other = self.temp_path / "other"
        other.mkdir()
        with (
            patch("app.gui.main_window.save_index_source"),
            patch.object(self.window, "_reset_views_for_source_change"),
            patch.object(self.window, "_start_background_indexing") as start,
        ):
            self.window.on_settings_data_path_changed(str(other))
        start.assert_called_once()
        with (
            patch.object(self.window.folder_page, "cleanup") as folder,
            patch.object(self.window.search_page, "reset") as search,
            patch.object(self.window.navigator, "reset") as navigation,
        ):
            self.window._reset_views_for_source_change()
        folder.assert_called_once_with()
        search.assert_called_once_with()
        navigation.assert_called_once_with("search")

    def test_index_job_workflow_states_and_recognition_messages(self):
        self.window.index_controller.is_active = Mock(return_value=False)
        self.window.index_controller.start = Mock()
        self.window.index_controller.cancel = Mock()
        self.window.index_controller.current_state = Mock(return_value={})
        self.window.content_job_controller.cancel = Mock()
        popup = Mock()
        self.window.settings_popup = popup
        source = self.temp_path / "source"
        source.mkdir()
        self.window.index_source = source

        self.window.index_controller.is_active.return_value = True
        self.window.on_settings_reindex_requested()
        popup.set_indexing.assert_called_with(True)
        self.window.index_controller.is_active.return_value = False
        self.window.index_manager.index_is_current.return_value = False
        with patch.object(self.window, "_start_background_indexing") as start:
            self.window.on_settings_reindex_requested()
        start.assert_called_once()

        missing = self.temp_path / "missing"
        self.window.index_source = missing
        with patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window.on_settings_reindex_requested()
        warning.assert_called_once()

        self.window.index_controller.is_active.return_value = False
        self.window.index_controller.start.side_effect = RuntimeError("broken")
        with patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window._start_background_indexing(source, True, "start")
        warning.assert_called_once()
        self.window.index_controller.start.side_effect = None
        self.window.on_indexing_progress(3, str(source / "file.txt"))
        popup.set_index_progress.assert_called_with(3, "file.txt")

        self.window.index_controller.is_active.return_value = True
        self.window.index_controller.current_state.return_value = {"source": str(source)}
        self.window.index_source = self.temp_path / "other"
        self.window.check_and_index()
        self.assertEqual(self.window.pending_index_source, source)
        self.window.content_job_controller.state_dir = self.temp_path
        with patch("app.gui.main_window.read_state", return_value={"status": "running"}):
            self.window.cancel_background_indexing()
        self.window.index_controller.cancel.assert_called()
        self.window.content_job_controller.cancel.assert_called()

        self.window.index_controller.is_active.return_value = False
        with patch.object(self.window, "_activate_built_index") as activate:
            self.window.on_index_ready({"build_path": str(source / "build.db")})
        activate.assert_called_once()
        self.window.on_index_ready({})
        with (
            patch.object(self.window, "_activate_built_index", side_effect=RuntimeError("locked")),
            patch("app.gui.main_window.QMessageBox.warning") as warning,
        ):
            self.window.on_index_ready({"build_path": "build.db"})
        warning.assert_called_once()

        for state in (
            {"status": "cancelled"},
            {"status": "error", "error": "bad"},
            {"status": "no_changes", "indexed_count": 4},
        ):
            with patch("app.gui.main_window.QMessageBox.warning"):
                self.window.on_indexing_complete(state)
        with patch.object(self.window, "_refresh_customer_results_only") as refresh:
            self.window.on_indexing_complete({
                "status": "no_changes", "customers_created": 1,
            })
        refresh.assert_called_once_with()

        messages = Mock()
        self.window.status_bar.set_text = messages
        self.window._show_customer_recognition_result({"customer_sync_error": "bad"})
        self.window._show_customer_recognition_result({
            "customer_cases_pending": 2, "customers_created": 1,
            "customers_assigned": 3,
        })
        self.window._show_customer_recognition_result({"customers_assigned": 1})
        self.assertEqual(messages.call_count, 3)

    def test_filesystem_options_backup_and_close_lifecycle(self):
        self.window.index_controller.is_active = Mock(return_value=False)
        source = self.temp_path / "source"
        source.mkdir()
        options = SimpleNamespace(
            automatic_monitoring_enabled=True,
            excluded_folder_names=(),
            change_delay_seconds=1,
            daily_reconciliation_enabled=True,
            content_search_enabled=True,
            content_indexing_enabled=True,
            resource_profile="balanced",
            preferred_document_patterns=(),
            priority_documents_per_project=1,
            newest_years_first=True,
            catalog_fingerprint=Mock(return_value="catalog"),
            content_fingerprint=Mock(return_value="content"),
        )
        self.window.index_options = options
        self.window.index_source = source
        monitor = Mock()
        with patch("app.gui.main_window.FileSystemMonitor", return_value=monitor):
            self.window._start_filesystem_monitor()
        monitor.start.assert_called_once_with()
        self.window.filesystem_monitor = monitor
        options.automatic_monitoring_enabled = False
        self.window._start_filesystem_monitor()
        monitor.requestInterruption.assert_called()

        self.window.index_controller.is_active.return_value = True
        self.window._on_filesystem_changes(SimpleNamespace(total=2))
        self.assertTrue(self.window.pending_filesystem_sync)
        self.window.index_controller.is_active.return_value = False
        with patch.object(self.window, "_start_incremental_filesystem_sync") as sync:
            self.window._on_filesystem_changes(SimpleNamespace(total=2))
            self.window._start_daily_catalog_reconciliation()
        self.assertEqual(sync.call_count, 2)
        with patch.object(self.window, "_start_background_indexing") as start:
            self.window._start_incremental_filesystem_sync()
        start.assert_called_once()

        changed = SimpleNamespace(**vars(options))
        changed.catalog_fingerprint = Mock(return_value="changed")
        changed.content_fingerprint = Mock(return_value="content")
        changed.automatic_monitoring_enabled = False
        with (
            patch("app.gui.main_window.save_index_options") as save,
            patch.object(self.window, "_start_filesystem_monitor"),
            patch.object(self.window, "_start_incremental_filesystem_sync") as sync,
        ):
            self.window.on_index_options_changed(changed)
        save.assert_called_once_with(changed)
        sync.assert_called_once_with()

        self.window.index_controller.is_active.return_value = True
        with patch("app.gui.main_window.QMessageBox.information") as information:
            self.window.on_load_backup_requested("backup.db")
        information.assert_called_once()
        self.window.index_controller.is_active.return_value = False
        self.window.catalog_store.create_restore_build = Mock(side_effect=RuntimeError("bad"))
        with patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window.on_load_backup_requested("backup.db")
        warning.assert_called_once()

        workers = []
        for attribute in (
            "filesystem_monitor", "settings_data_worker", "statistics_worker",
            "content_maintenance_worker", "blacklist_cleanup_worker",
        ):
            worker = Mock()
            worker.isRunning.return_value = True
            setattr(self.window, attribute, worker)
            workers.append(worker)
        self.window.search_workers = {Mock()}
        self.window.tray_icon = Mock()
        self.window.index_tray_window = Mock()
        event = Mock()
        with patch("app.gui.main_window.DocumentConverter.clear_word_preview_cache"):
            self.window.closeEvent(event)
        event.accept.assert_called_once_with()

    def test_tray_window_filter_settings_and_dialog_workflows(self):
        tray = Mock()
        with (
            patch("app.gui.main_window.QSystemTrayIcon") as tray_class,
            patch("app.gui.main_window.QMenu") as menu_class,
        ):
            tray_class.isSystemTrayAvailable.return_value = True
            tray_class.return_value = tray
            actions = [Mock(), Mock(), Mock()]
            menu_class.return_value.addAction.side_effect = actions
            ORIGINAL_INIT_SYSTEM_TRAY(self.window)
        tray.show.assert_called_once_with()

        tray_window = Mock()
        with patch("app.gui.main_window.IndexTrayWindow", return_value=tray_window):
            self.window.index_tray_window = None
            self.window._show_index_tray_window()
        tray_window.show_status.assert_called_once_with()
        self.window._show_index_tray_window()
        self.assertEqual(tray_window.show_status.call_count, 2)

        with patch.object(self.window, "showNormal") as normal, \
                patch.object(self.window, "raise_") as raise_window, \
                patch.object(self.window, "activateWindow") as activate:
            self.window._show_from_tray()
        normal.assert_called_once_with()
        raise_window.assert_called_once_with()
        activate.assert_called_once_with()
        with patch.object(self.window, "isVisible", return_value=True), \
                patch.object(self.window, "isMinimized", return_value=False), \
                patch.object(self.window, "hide") as hide:
            self.window._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        hide.assert_called_once_with()
        self.window._on_tray_activated(QSystemTrayIcon.ActivationReason.Unknown)

        self.window.open_filter_popup()
        self.assertTrue(self.window.filter_popup.isVisible())
        self.window.open_filter_popup()
        self.assertFalse(self.window.filter_popup.isVisible())
        self.window.index_tray_window = tray_window
        with patch.object(self.window.theme_manager, "apply") as apply:
            self.window.apply_theme()
        apply.assert_called_once()
        tray_window.update.assert_called_once_with()

        popup = Mock()
        popup.isVisible.return_value = True
        self.window.settings_popup = popup
        self.window.open_settings_popup()
        popup.close.assert_called_once_with()
        self.window._clear_settings_popup()
        self.assertIsNone(self.window.settings_popup)
        self.window._center_settings_popup()

        review = Mock()
        with patch("app.gui.main_window.CustomerRecognitionReviewDialog", return_value=review), \
                patch.object(self.window, "_refresh_customer_results_only") as refresh:
            self.window.open_customer_recognition_review()
        review.exec.assert_called_once_with()
        refresh.assert_called_once_with()

    def test_search_refresh_full_launch_and_theme_guard_paths(self):
        self.window.header.set_query("")
        with patch.object(self.window, "_show_initial_customers") as initial:
            self.window._refresh_customer_results_only()
        initial.assert_called_once_with()
        self.window.header.set_query("query")
        self.repository.search.return_value = [Customer(id=1, display_name="One")]
        self.window.search_page.set_customers = Mock()
        self.window._refresh_customer_results_only()
        self.window.search_page.set_customers.assert_called_once()

        self.window.header.set_query("query")
        self.window.index_options.content_search_enabled = True
        worker = Mock()
        with patch("app.gui.main_window.SearchWorker", return_value=worker):
            self.window.start_full_search()
        self.assertEqual(worker.start.call_count, 3)
        self.window.search_workers.clear()

        self.window.index_options.content_search_enabled = False
        self.window.search_counts = {"customers": None, "folders": None, "text": None}
        self.window._update_search_status()
        self.window.filter_popup.sort_combo.setCurrentIndex(-1)
        filters = self.window._current_search_filters()
        self.assertEqual(filters.sort_order, SearchSort.RELEVANCE)
        with patch("app.gui.main_window.QApplication.instance", return_value=None):
            self.window.apply_theme()

    def test_content_controls_release_workers_and_window_events(self):
        self.window.content_job_controller.is_active = Mock(return_value=True)
        self.window.pause_content_indexing()
        self.assertIn("pausiert", self.window.status_bar.status_label.text())
        self.window.content_job_controller.is_active.return_value = False
        self.window.pause_content_indexing()
        self.window.index_options.content_indexing_enabled = False
        self.window.resume_content_indexing()
        self.window.index_options.content_indexing_enabled = True
        self.window.content_job_controller.resume = Mock(return_value=True)
        self.window.resume_content_indexing()

        with patch.object(self.window, "_settings_question", return_value=QMessageBox.No), \
                patch.object(self.window, "_start_content_maintenance") as maintenance:
            self.window.confirm_rebuild_content_index()
            self.window.confirm_clear_content_index()
        maintenance.assert_not_called()
        with patch.object(self.window, "_settings_question", return_value=QMessageBox.Yes), \
                patch.object(self.window, "_start_content_maintenance") as maintenance:
            self.window.confirm_rebuild_content_index()
            self.window.confirm_clear_content_index()
        self.assertEqual(maintenance.call_count, 2)

        popup = Mock()
        popup.isVisible.return_value = True
        self.window.settings_popup = popup
        self.window.open_index_diagnostics()
        popup.nav_list.setCurrentRow.assert_called_once_with(1)
        self.window.settings_popup = None
        with patch.object(self.window, "open_settings_popup") as open_popup:
            self.window.open_index_diagnostics()
        open_popup.assert_called_once_with()

        for worker_type, attribute, releaser in (
            ("StatisticsWorker", "statistics_worker", "_release_statistics_worker"),
            ("SettingsDataWorker", "settings_data_worker", "_release_settings_data_worker"),
            ("BlacklistCleanupWorker", "blacklist_cleanup_worker", "_release_blacklist_cleanup_worker"),
        ):
            worker = Mock()
            setattr(self.window, attribute, worker)
            with patch(f"app.gui.main_window.{worker_type}", new=type(worker)), \
                    patch.object(self.window, "sender", return_value=worker):
                getattr(self.window, releaser)()

        popup = Mock()
        popup.isVisible.return_value = True
        popup.size_for_parent.return_value = QSize(700, 500)
        self.window.settings_popup = popup
        with patch("app.gui.main_window.QTimer.singleShot"):
            self.window.resizeEvent(QResizeEvent(QSize(1000, 700), QSize(900, 650)))
            self.window.moveEvent(QMoveEvent(QPoint(10, 10), QPoint(0, 0)))
        popup.resize.assert_called_once()

    def test_index_activation_reconciliation_monitor_and_option_switches(self):
        catalog_manager = Mock(db_path=self.temp_path / "catalog.db")
        self.window.catalog_store = Mock()
        self.window.search_workers = {Mock()}
        with patch.object(self.window, "_open_active_index", return_value=catalog_manager), \
                patch.object(self.window, "_reconcile_content_queue") as reconcile, \
                patch.object(self.window, "_refresh_search_facets"), \
                patch.object(self.window, "_refresh_statistics"):
            self.window._activate_built_index(self.temp_path / "build.db")
        reconcile.assert_called_once_with()

        self.window.index_manager = Mock()
        self.window._reconcile_content_queue()
        source = self.temp_path / "source"
        source.mkdir()
        self.window.index_source = source
        self.window.index_controller.is_active = Mock(return_value=True)
        self.window._start_daily_catalog_reconciliation()
        self.window.index_controller.is_active.return_value = False
        missing = self.temp_path / "missing"
        self.window.index_source = missing
        self.window._start_daily_catalog_reconciliation()

        self.window.index_controller.is_active.return_value = True
        self.window._start_incremental_filesystem_sync()
        self.assertTrue(self.window.pending_filesystem_sync)

        base = self.window.index_options
        base.catalog_fingerprint = Mock(return_value="same")
        base.content_fingerprint = Mock(return_value="same")
        base.resource_profile = "balanced"
        base.maximum_parallel_shards = 2
        base.content_indexing_enabled = True
        changed = SimpleNamespace(**vars(base))
        changed.catalog_fingerprint = Mock(return_value="same")
        changed.content_fingerprint = Mock(return_value="same")
        changed.resource_profile = "fast"
        changed.maximum_parallel_shards = 3
        changed.content_indexing_enabled = True
        with patch("app.gui.main_window.save_index_options"), \
                patch.object(self.window, "_start_filesystem_monitor"), \
                patch.object(self.window.content_job_controller, "is_active", return_value=True):
            self.window.on_index_options_changed(changed)
        self.assertTrue(self.window.pending_content_restart)

    def test_remaining_callbacks_reader_releases_and_completed_index_flow(self):
        self.window.navigator.navigate("customer", 1)
        with patch.object(self.window, "_refresh_statistics") as statistics, \
                patch.object(self.window, "_refresh_customer_results_only") as refresh, \
                patch.object(self.window, "_show_route") as route:
            self.window._on_customer_changed(1)
        statistics.assert_called_once_with()
        refresh.assert_called_once_with()
        route.assert_called_once()

        self.window.search_generation = 1
        self.window.search_page.set_document_error = Mock()
        self.window._on_search_completed(1, "unknown", None, "bad")
        result = SearchPage([{"path": "/x"}], 1, 1, 1)
        self.window.search_page.set_documents = Mock()
        self.window.search_page.set_document_coverage = Mock()
        self.window._on_search_completed(1, "text", result, "")
        self.window.search_page.set_documents.assert_called_once()

        self.window.settings_popup = None
        with patch("app.gui.main_window.QMessageBox.warning", return_value=5):
            self.assertEqual(self.window._settings_warning("T", "M"), 5)
        self.window.settings_data_worker = Mock(isRunning=Mock(return_value=False))
        self.window.statistics_worker = Mock(isRunning=Mock(return_value=False))
        self.window._wait_for_content_index_readers()
        self.window.settings_popup = None
        with patch.object(self.window, "_refresh_statistics") as refresh_stats, \
                patch.object(self.window, "_start_content_indexing_if_enabled") as restart:
            self.window._on_content_maintenance_complete("clear", {}, "")
            self.window._on_content_maintenance_complete("optimize", {}, "")
        self.assertEqual(refresh_stats.call_count, 2)
        restart.assert_called_once_with()

        class DummyWorker:
            def __init__(self):
                self.deleted = False

            def deleteLater(self):
                self.deleted = True

        for class_name, attribute, method_name in (
            ("StatisticsWorker", "statistics_worker", "_release_statistics_worker"),
            ("SettingsDataWorker", "settings_data_worker", "_release_settings_data_worker"),
            ("BlacklistCleanupWorker", "blacklist_cleanup_worker", "_release_blacklist_cleanup_worker"),
        ):
            worker = DummyWorker()
            setattr(self.window, attribute, worker)
            with patch(f"app.gui.main_window.{class_name}", DummyWorker), \
                    patch.object(self.window, "sender", return_value=worker):
                getattr(self.window, method_name)()
            self.assertTrue(worker.deleted)
            self.assertIsNone(getattr(self.window, attribute))

    def test_blacklist_popup_check_index_completed_reload_and_restore_success(self):
        field = Mock()
        popup = Mock()
        popup.recognition_blacklist_fields = {"email_blacklist": field}
        self.window.settings_popup = popup
        self.window.recognition_options = CustomerRecognitionOptions()
        self.window.customer_repository = Mock()
        with patch("app.gui.main_window.save_customer_recognition_options"), \
                patch.object(self.window, "_cleanup_blacklisted_values"), \
                patch.object(self.window, "_refresh_settings_popup_data") as refresh:
            self.window.confirm_blacklist_suggestion(1, "email", "x@example.test")
            self.window.dismiss_blacklist_suggestion(2)
        field.setPlainText.assert_called_once()
        self.assertEqual(refresh.call_count, 2)
        with patch("app.gui.main_window.CustomerRecognitionReviewDialog") as review, \
                patch.object(self.window, "_refresh_customer_results_only"):
            self.window.open_customer_recognition_review()
        popup.close.assert_called()
        review.return_value.exec.assert_called_once()

        self.window.index_controller.is_active = Mock(return_value=False)
        self.window.index_source = self.temp_path / "missing"
        with patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window.check_and_index()
        warning.assert_called_once()
        source = self.temp_path / "source"
        source.mkdir()
        self.window.index_source = source
        self.window.index_manager.index_is_current.return_value = True
        with patch.object(self.window, "_start_background_indexing") as start:
            self.window.check_and_index()
        start.assert_called_once_with(
            source, full_rebuild=False,
            status_text="Prüfe Datenquelle im Hintergrund auf Änderungen …",
        )

        self.window.pending_index_source = source
        with patch.object(self.window, "_reload_active_index") as reload_index, \
                patch("app.gui.main_window.save_index_source"), \
                patch.object(self.window, "_start_filesystem_monitor"), \
                patch.object(self.window, "_show_initial_customers"), \
                patch.object(self.window, "_show_customer_recognition_result"), \
                patch.object(self.window, "_refresh_settings_popup_data"), \
                patch.object(self.window, "_start_content_indexing_if_enabled"):
            self.window.on_indexing_complete({
                "status": "completed", "activated_by": "worker", "indexed_count": 2,
            })
        reload_index.assert_called_once_with()

        connection = Mock()
        connection.conn.execute.return_value = [("index_root", str(source))]
        self.window.catalog_store.create_restore_build = Mock(return_value=self.temp_path / "build.db")
        self.window.index_controller.is_active.return_value = False
        with patch("app.gui.main_window.CatalogIndexManager", return_value=connection), \
                patch.object(self.window, "_activate_built_index"), \
                patch.object(self.window, "_start_content_indexing_if_enabled"), \
                patch("app.gui.main_window.save_index_source"), \
                patch.object(self.window, "_show_initial_customers"), \
                patch.object(self.window, "_refresh_settings_popup_data"):
            self.window.on_load_backup_requested("backup.db")
        connection.close.assert_called_once_with()

    def test_catalog_reconcile_reload_monitor_missing_and_runtime_option_paths(self):
        manager = Mock(db_path=self.temp_path / "catalog.db")
        self.window.index_manager = manager
        with patch("app.gui.main_window.CatalogIndexManager", new=type(manager)), \
                patch("app.gui.main_window.ContentStateRepository.open_recoverable") as opened, \
                patch("app.gui.main_window.ShardRepository"):
            opened.return_value.__enter__.return_value = Mock()
            self.window._reconcile_content_queue()

        replacement = Mock(db_path=self.temp_path / "replacement.db")
        self.window.search_workers = {Mock()}
        with patch.object(self.window, "_open_active_index", return_value=replacement), \
                patch.object(self.window, "_refresh_search_facets"), \
                patch.object(self.window, "_refresh_statistics"):
            self.window._reload_active_index()
        self.assertIs(self.window.index_manager, replacement)

        self.window.filesystem_monitor = None
        self.window.index_options.automatic_monitoring_enabled = True
        self.window.index_source = self.temp_path / "missing"
        self.window._start_filesystem_monitor()

        base = self.window.index_options
        base.catalog_fingerprint = Mock(return_value="same")
        base.content_fingerprint = Mock(return_value="same")
        base.resource_profile = "balanced"
        base.maximum_parallel_shards = 2
        base.content_indexing_enabled = True
        changed = SimpleNamespace(**vars(base))
        changed.catalog_fingerprint = Mock(return_value="same")
        changed.content_fingerprint = Mock(return_value="same")
        changed.resource_profile = "fast"
        changed.maximum_parallel_shards = 3
        changed.content_indexing_enabled = True
        with patch("app.gui.main_window.save_index_options"), \
                patch.object(self.window, "_start_filesystem_monitor"), \
                patch.object(self.window.content_job_controller, "is_active", return_value=False), \
                patch.object(self.window, "_start_content_indexing_if_enabled") as start:
            self.window.on_index_options_changed(changed)
        start.assert_called_once_with()

        switched = SimpleNamespace(**vars(changed))
        switched.catalog_fingerprint = Mock(return_value="same")
        switched.content_fingerprint = Mock(return_value="same")
        switched.resource_profile = "fast"
        switched.maximum_parallel_shards = 3
        switched.content_indexing_enabled = False
        with patch("app.gui.main_window.save_index_options"), \
                patch.object(self.window, "_start_filesystem_monitor"):
            self.window.on_index_options_changed(switched)

    def test_final_main_window_branch_matrix(self):
        self.window.index_controller.start = Mock()
        legacy = Mock()
        with patch("app.gui.main_window.CATALOG_DB_FILE", self.temp_path / "absent.db"), \
                patch("app.gui.main_window.IndexManager", return_value=legacy):
            self.assertIs(ORIGINAL_OPEN_ACTIVE_INDEX(self.window), legacy)

        self.window.settings_popup = None
        self.window._on_content_progress({"status": "idle"})
        self.window._on_content_finished({"status": "idle"})
        with patch("app.gui.main_window.has_configured_index_source", return_value=True), \
                patch.object(self.window, "check_and_index") as check, \
                patch.object(self.window, "_start_filesystem_monitor") as monitor:
            self.window._initialize_data_source()
        check.assert_called_once_with()
        monitor.assert_called_once_with()

        self.window.index_controller.current_state = Mock(return_value={})
        self.window.index_manager.get_metadata.return_value = ""
        with patch("app.gui.main_window.has_configured_index_source", return_value=False):
            self.window._reconcile_source_from_completed_job()
        self.window._show_route(NavigationEntry("unknown"))

        folder = self.temp_path / "folder"
        folder.mkdir()
        with patch("app.gui.main_window.QDesktopServices.openUrl", return_value=True), \
                patch("app.gui.main_window.QMessageBox.warning") as warning:
            self.window.open_native_path(str(folder))
        warning.assert_not_called()
        dialog = Mock(exec=Mock(return_value=True))
        self.repository.get_by_folder.return_value = None
        with patch("app.gui.main_window.CustomerEditorDialog", return_value=dialog), \
                patch.object(self.window, "_refresh_customer_results_only"):
            self.window.manage_folder_customer("/folder", "Folder")
        self.window.navigator.reset("search")
        with patch.object(self.window, "_refresh_statistics"), \
                patch.object(self.window, "_refresh_customer_results_only"), \
                patch.object(self.window, "_show_route") as route:
            self.window._on_customer_changed(1)
        route.assert_not_called()

        self.window.search_generation = 1
        self.window._on_search_completed(
            1, "unknown", SearchPage([], 0, 1, 1), ""
        )
        self.window.content_job_controller.resume = Mock(return_value=False)
        self.window.index_options.content_indexing_enabled = True
        self.window.resume_content_indexing()

        self.window.statistics_refresh_pending = True
        with patch.object(self.window, "sender", return_value=None), \
                patch.object(self.window, "_refresh_statistics") as refresh:
            self.window._release_statistics_worker()
        refresh.assert_called_once_with()
        self.window._on_settings_data_loaded({})
        with patch.object(self.window, "sender", return_value=None):
            self.window._release_settings_data_worker()
            self.window._release_blacklist_cleanup_worker()

        replacement = Mock()
        self.window.customer_repository = Mock()
        self.window.settings_popup = None
        with patch("app.gui.main_window.CustomerRepository", return_value=replacement), \
                patch.object(self.window, "_show_initial_customers"), \
                patch.object(self.window, "_refresh_statistics"):
            self.window.clear_customer_data()

        popup = Mock()
        popup.recognition_blacklist_fields = {}
        self.window.settings_popup = popup
        self.window.recognition_options = CustomerRecognitionOptions()
        self.window.customer_repository = Mock()
        with patch("app.gui.main_window.save_customer_recognition_options"), \
                patch.object(self.window, "_cleanup_blacklisted_values"), \
                patch.object(self.window, "_refresh_settings_popup_data"):
            self.window.confirm_blacklist_suggestion(1, "email", "x")
        self.window.settings_popup = None
        self.window.resizeEvent(QResizeEvent(QSize(1000, 700), QSize(900, 650)))
        self.window.moveEvent(QMoveEvent(QPoint(1, 1), QPoint(0, 0)))

        self.window.index_controller.is_active = Mock(return_value=True)
        self.window.settings_popup = None
        self.window.on_settings_reindex_requested()
        self.window.index_controller.is_active.return_value = False
        self.window.index_source = self.temp_path / "missing"
        with patch("app.gui.main_window.QMessageBox.warning"):
            self.window.on_settings_reindex_requested()
        self.window._start_background_indexing(folder, False, "ignored")
        self.window.on_indexing_progress(1, "/tmp/file")
        with patch("app.gui.main_window.read_state", return_value={}):
            self.window.cancel_background_indexing()

        self.window.index_controller.current_state.return_value = {}
        self.window.index_source = folder
        self.window.check_and_index()
        self.window.index_controller.current_state.return_value = {"source": str(folder)}
        self.window.check_and_index()

        self.window.settings_popup = None
        self.window.pending_index_source = None
        with patch("app.gui.main_window.save_index_source"), \
                patch.object(self.window, "_show_initial_customers"), \
                patch.object(self.window, "_show_customer_recognition_result"), \
                patch.object(self.window, "_start_content_indexing_if_enabled"):
            self.window.on_indexing_complete({
                "status": "completed", "source": str(folder), "indexed_count": 1,
            })
        self.window.pending_filesystem_sync = True
        with patch("app.gui.main_window.QTimer.singleShot") as later:
            self.window.on_indexing_complete({"status": "cancelled"})
        later.assert_called_once()

        base = self.window.index_options
        base.catalog_fingerprint = Mock(return_value="same")
        base.content_fingerprint = Mock(return_value="same")
        base.resource_profile = "fast"
        base.maximum_parallel_shards = 3
        base.content_indexing_enabled = False
        enabled = SimpleNamespace(**vars(base))
        enabled.catalog_fingerprint = Mock(return_value="same")
        enabled.content_fingerprint = Mock(return_value="same")
        enabled.resource_profile = "fast"
        enabled.maximum_parallel_shards = 3
        enabled.content_indexing_enabled = True
        with patch("app.gui.main_window.save_index_options"), \
                patch.object(self.window, "_start_filesystem_monitor"), \
                patch.object(self.window, "_start_content_indexing_if_enabled") as start:
            self.window.on_index_options_changed(enabled)
        start.assert_called_once_with()

        self.window.index_controller.is_active.return_value = False
        connection = Mock()
        connection.conn.execute.return_value = []
        self.window.catalog_store.create_restore_build = Mock(return_value=self.temp_path / "build")
        self.window.settings_popup = None
        with patch("app.gui.main_window.CatalogIndexManager", return_value=connection), \
                patch.object(self.window, "_activate_built_index"), \
                patch.object(self.window, "_start_content_indexing_if_enabled"), \
                patch.object(self.window, "_show_initial_customers"):
            self.window.on_load_backup_requested("backup")

        self.window.index_controller.is_active.return_value = True
        self.window._start_background_indexing(folder, False, "active")
        self.window.index_controller.is_active.return_value = False
        self.window.settings_popup = None
        self.window.index_controller.start.side_effect = RuntimeError("bad")
        with patch("app.gui.main_window.QMessageBox.warning"):
            self.window._start_background_indexing(folder, False, "error")
        self.window.index_controller.start.side_effect = None

        self.window.index_controller.is_active.return_value = True
        self.window.index_controller.current_state.return_value = {}
        self.window.check_and_index()
        self.window.index_controller.current_state.return_value = {"source": str(folder)}
        self.window.index_source = folder
        self.window.check_and_index()

        self.window.index_controller.is_active.return_value = False
        self.window.pending_index_source = None
        with patch.object(self.window, "_show_initial_customers"), \
                patch.object(self.window, "_show_customer_recognition_result"), \
                patch.object(self.window, "_start_content_indexing_if_enabled"):
            self.window.on_indexing_complete({"status": "completed", "indexed_count": 0})

        popup = Mock()
        self.window.settings_popup = popup
        self.window._on_settings_data_loaded({})
        worker = Mock()
        self.window.statistics_worker = Mock()
        self.window.statistics_refresh_pending = False
        with patch("app.gui.main_window.StatisticsWorker", new=type(worker)), \
                patch.object(self.window, "sender", return_value=worker):
            self.window._release_statistics_worker()
        cleanup = Mock()
        self.window.blacklist_cleanup_worker = Mock()
        with patch("app.gui.main_window.BlacklistCleanupWorker", new=type(cleanup)), \
                patch.object(self.window, "sender", return_value=cleanup):
            self.window._release_blacklist_cleanup_worker()

        event = Mock()
        self.window.settings_popup = None
        self.window.filesystem_monitor = None
        self.window.settings_data_worker = None
        self.window.statistics_worker = None
        self.window.content_maintenance_worker = None
        self.window.blacklist_cleanup_worker = None
        self.window.search_workers = set()
        with patch(
            "app.gui.main_window.DocumentConverter.clear_word_preview_cache",
            side_effect=RuntimeError("cleanup"),
        ):
            self.window.closeEvent(event)
        event.accept.assert_called_once_with()

    def test_constructor_starts_content_when_catalog_exists(self):
        catalog = self.temp_path / "catalog.db"
        catalog.touch()
        with patch("app.gui.main_window.CATALOG_DB_FILE", catalog), \
                patch.object(MainWindow, "_initialize_data_source"), \
                patch.object(MainWindow, "_reconcile_source_from_completed_job"), \
                patch.object(MainWindow, "_refresh_search_facets"), \
                patch.object(MainWindow, "_refresh_statistics"), \
                patch.object(MainWindow, "_start_content_indexing_if_enabled") as start:
            other = MainWindow()
        start.assert_called_once_with()
        other.folder_page.cleanup = Mock()
        other.close()
        other.deleteLater()

    def test_constructor_without_catalog_and_final_geometry_status_arcs(self):
        missing_catalog = self.temp_path / "missing-catalog.db"
        with patch("app.gui.main_window.CATALOG_DB_FILE", missing_catalog), \
                patch.object(MainWindow, "_initialize_data_source"), \
                patch.object(MainWindow, "_reconcile_source_from_completed_job"), \
                patch.object(MainWindow, "_refresh_search_facets"), \
                patch.object(MainWindow, "_refresh_statistics"), \
                patch.object(MainWindow, "_start_content_indexing_if_enabled") as start:
            other = MainWindow()
        start.assert_not_called()
        other.folder_page.cleanup = Mock()
        other.close()
        other.deleteLater()

        self.window.filter_popup.close()
        screen = Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 10000, 10000)
        with patch.object(self.window, "screen", return_value=screen), \
                patch.object(
                    self.window.header.filter_button,
                    "mapToGlobal",
                    return_value=QPoint(10, 10),
                ):
            self.window.open_filter_popup()
        self.window.filter_popup.close()

        self.window.settings_popup = None
        self.window.pending_filesystem_sync = False
        self.window.on_indexing_complete({"status": "other"})
