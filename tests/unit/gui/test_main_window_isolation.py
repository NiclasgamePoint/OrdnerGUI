from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.gui.main_window import MainWindow
from tests.base.main_window_test_case import (
    InertContentJobController,
    InertFileSystemMonitor,
    InertIndexJobController,
    InertStatisticsWorker,
    MainWindowTestCase,
)


class MainWindowIsolationTests(MainWindowTestCase):
    def test_external_indexer_prevents_gui_index_writers(self):
        with (
            patch.object(MainWindow, "_initialize_data_source"),
            patch("app.gui.main_window.external_indexer_enabled", return_value=True),
        ):
            window = MainWindow()
            window.index_options.content_indexing_enabled = True
            window._start_background_indexing(
                self.main_window_source_path,
                full_rebuild=False,
                status_text="Test",
            )
            window._start_filesystem_monitor()
            self.assertFalse(window._start_content_indexing_if_enabled())

        self.assertEqual(window.index_controller.start_calls, [])
        self.assertIsNone(window.filesystem_monitor)
        self.assertEqual(window.content_job_controller.start_calls, 0)
        window.close()

    def test_catalog_opening_and_shutdown_worker_runtime_edges(self):
        self.main_window_catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.main_window_catalog_path.touch()
        owner = SimpleNamespace(index_options=Mock())
        manager = Mock()
        with patch("app.gui.main_window.CatalogIndexManager", return_value=manager) as manager_type:
            self.assertIs(MainWindow._open_active_index(owner), manager)
        manager_type.assert_called_once_with(
            self.main_window_catalog_path,
            options=owner.index_options,
        )

        worker = Mock()
        worker.isRunning.return_value = False
        MainWindow._stop_worker_for_shutdown(worker)
        worker.requestInterruption.assert_not_called()

        worker.isRunning.side_effect = RuntimeError
        MainWindow._stop_worker_for_shutdown(worker)

        worker = Mock()
        worker.isRunning.return_value = True
        worker.requestInterruption.side_effect = RuntimeError
        worker.wait.side_effect = RuntimeError
        MainWindow._stop_worker_for_shutdown(worker)
        worker.requestInterruption.assert_called_once_with()
        worker.wait.assert_called_once_with()

    def test_runtime_paths_and_background_boundaries_are_private_and_inert(self):
        with (
            patch.object(MainWindow, "_initialize_data_source"),
            patch("subprocess.Popen", side_effect=AssertionError("real process")),
        ):
            window = MainWindow()

        self.assertEqual(window.index_manager.db_path, self.main_window_database_path)
        self.assertEqual(
            window.customer_repository.database_path,
            self.main_window_customer_database_path,
        )
        self.assertIsInstance(window.index_controller, InertIndexJobController)
        self.assertIsInstance(
            window.content_job_controller, InertContentJobController
        )
        self.assertIsInstance(window.statistics_worker, InertStatisticsWorker)

        window.index_source = self.main_window_source_path
        window._start_background_indexing(
            self.main_window_source_path,
            full_rebuild=False,
            status_text="Test",
        )
        window._start_filesystem_monitor()
        window.index_options.content_indexing_enabled = True
        window._start_content_indexing_if_enabled()

        self.assertEqual(len(window.index_controller.start_calls), 1)
        self.assertIsInstance(window.filesystem_monitor, InertFileSystemMonitor)
        self.assertEqual(window.filesystem_monitor.start_calls, 1)
        self.assertEqual(window.content_job_controller.start_calls, 1)
        self.assertFalse(self.main_window_catalog_path.exists())

        monitor = window.filesystem_monitor
        statistics_worker = window.statistics_worker
        controller = window.index_controller
        content_controller = window.content_job_controller
        window.close()
        self.app.processEvents()

        self.assertEqual(monitor.request_interruption_calls, 1)
        self.assertEqual(monitor.wait_calls, 1)
        self.assertEqual(statistics_worker.request_interruption_calls, 1)
        self.assertEqual(statistics_worker.wait_calls, 1)
        self.assertEqual(controller.release_owner_calls, 1)
        self.assertEqual(content_controller.stop_observing_calls, 1)
