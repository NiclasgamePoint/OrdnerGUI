from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sqlite3
import time
import unittest
from unittest.mock import patch
import subprocess
import sys

from PySide6.QtWidgets import QApplication

from app.core.index_job_state import read_state, write_state
from app.core.config import CustomerRecognitionOptions
from app.core.customer_repository import CustomerRepository
from app.core.index_manager import IndexManager
from app.core.index_layout import IndexLayout
from app.gui.workers.index_job_controller import IndexJobController
from app.services.index_job import IndexJobRunner


class DetachedIndexJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_job_activates_index_after_gui_owner_is_released(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = source / "DEKRA" / "2026" / "Muster"
            project.mkdir(parents=True)
            (project / "eins.txt").write_text("Erster Inhalt", encoding="utf-8")
            (project / "zwei.txt").write_text("Zweiter Inhalt", encoding="utf-8")
            active = root / "index.db"
            state_dir = root / "state"

            controller = IndexJobController(active, state_dir)
            self.assertTrue(controller.start(source, full_rebuild=False))
            controller.release_owner()  # Simulates closing the GUI immediately.

            deadline = time.monotonic() + 20
            state = {}
            while time.monotonic() < deadline:
                state = read_state(state_dir)
                if state.get("status") in {"completed", "error", "cancelled"}:
                    break
                time.sleep(0.05)
            controller.poll()

            self.assertEqual(state.get("status"), "completed", state)
            self.assertEqual(state.get("activated_by"), "worker")
            self.assertTrue(active.exists())
            manager = IndexManager(active, initialize=False)
            self.assertEqual(manager.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 2)
            manager.close()

    def test_split_job_activates_catalog_then_prepares_content_queue(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = source / "DEKRA" / "2026" / "Muster"
            project.mkdir(parents=True)
            (project / "Angebot.txt").write_text("Dokumentinhalt", encoding="utf-8")
            layout = IndexLayout(root / "data" / "index")
            runner = IndexJobRunner(
                "split-test",
                layout.catalog_path,
                source,
                layout.jobs_dir / "catalog",
                True,
                root / "data" / "customers.db",
            )

            with patch.object(runner, "_start_content_job"):
                result = runner.run()

            self.assertEqual(result, 0)
            self.assertTrue(layout.catalog_path.exists())
            self.assertTrue(layout.content_state_path.exists())
            connection = sqlite3.connect(layout.catalog_path)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            connection.close()
            self.assertNotIn("file_content_fts", tables)
            state = sqlite3.connect(layout.content_state_path)
            self.assertEqual(
                state.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1
            )
            state.close()

    def test_split_job_archives_legacy_indexes_only_after_new_catalog_exists(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = IndexLayout(root / "data" / "index")
            layout.ensure_directories()
            legacy = root / "data" / "index.db"
            backup = root / "data" / "index.backup.1.db"
            legacy.write_bytes(b"legacy")
            backup.write_bytes(b"backup")
            runner = IndexJobRunner(
                "archive-test",
                layout.catalog_path,
                root,
                layout.jobs_dir / "catalog",
                True,
                root / "data" / "customers.db",
            )

            runner._archive_legacy_index()
            self.assertTrue(legacy.exists())
            layout.catalog_path.parent.mkdir(parents=True, exist_ok=True)
            layout.catalog_path.write_bytes(b"catalog")
            runner._archive_legacy_index()

            self.assertFalse(legacy.exists())
            self.assertFalse(backup.exists())
            self.assertEqual(
                (layout.legacy_dir / "index.db").read_bytes(), b"legacy"
            )
            self.assertEqual(
                (layout.legacy_dir / "index.backup.1.db").read_bytes(), b"backup"
            )

    def test_detached_runner_recognizes_customers_after_activation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = source / "DEKRA" / "2026" / "Müller, Berlin"
            project.mkdir(parents=True)
            (project / "eins.txt").write_text(
                "Kunde: Max Müller",
                encoding="utf-8",
            )
            active = root / "index.db"
            customers = root / "customers.db"
            state_dir = root / "state"
            runner = IndexJobRunner(
                "customer-test",
                active,
                source,
                state_dir,
                True,
                customers,
            )

            with patch(
                "app.services.index_job.load_customer_recognition_options",
                return_value=CustomerRecognitionOptions(enabled=True),
            ):
                result = runner.run()

            state = read_state(state_dir)
            repository = CustomerRepository(customers)
            self.assertEqual(result, 0)
            self.assertEqual(state.get("status"), "completed")
            self.assertEqual(state.get("activated_by"), "worker")
            self.assertEqual(state.get("customers_created"), 1)
            self.assertEqual(len(repository.list_customers()), 1)
            repository.close()

    def test_customer_sync_error_does_not_rollback_activated_index(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = source / "DEKRA" / "2026" / "Muster, Köln"
            project.mkdir(parents=True)
            (project / "eins.txt").write_text("Inhalt", encoding="utf-8")
            active = root / "index.db"
            state_dir = root / "state"
            runner = IndexJobRunner(
                "error-test",
                active,
                source,
                state_dir,
                True,
                root / "customers.db",
            )

            with patch(
                "app.services.index_job.CustomerRecognitionService.synchronize",
                side_effect=RuntimeError("Kundentestfehler"),
            ):
                with self.assertLogs("app.services.index_job", level="ERROR"):
                    result = runner.run()

            state = read_state(state_dir)
            self.assertEqual(result, 0)
            self.assertTrue(active.exists())
            self.assertEqual(state.get("status"), "completed")
            self.assertIn("Kundentestfehler", state.get("customer_sync_error", ""))

    def test_controller_can_adopt_and_finish_existing_job_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir(parents=True)
            active = root / "index.db"
            state_dir = root / "state"
            controller = IndexJobController(active, state_dir)

            finished_states = []
            controller.finished.connect(lambda state: finished_states.append(state))

            write_state(state_dir, {
                "job_id": "adopt-job",
                "pid": os.getpid(),
                "status": "running",
                "source": str(source),
                "active_path": str(active),
            })

            self.assertTrue(controller.adopt_running_job())

            write_state(state_dir, {
                "job_id": "adopt-job",
                "pid": os.getpid(),
                "status": "completed",
                "source": str(source),
                "active_path": str(active),
            })
            controller.poll()
            self.assertEqual(len(finished_states), 1)
            self.assertEqual(finished_states[0].get("status"), "completed")

    def test_cancel_uses_control_file_without_signalling_hidden_process(self):
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            controller = IndexJobController(Path(directory) / "index.db", state_dir)
            write_state(state_dir, {
                "job_id": "cancel-job",
                "pid": os.getpid(),
                "status": "running",
            })

            with patch("app.gui.workers.index_job_controller.os.kill") as kill:
                controller.cancel()

            self.assertTrue((state_dir / "index_job.cancel").exists())
            kill.assert_not_called()

    def test_state_write_retries_a_temporary_windows_file_lock(self):
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            real_replace = os.replace
            attempts = 0

            def temporarily_locked(source, target):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("temporär gesperrt")
                return real_replace(source, target)

            with patch(
                "app.core.index_job_state.os.replace",
                side_effect=temporarily_locked,
            ), patch("app.core.index_job_state.time.sleep"):
                write_state(state_dir, {"status": "running"})

            self.assertEqual(attempts, 3)
            self.assertEqual(read_state(state_dir).get("status"), "running")

    def test_progress_writes_a_new_path_even_inside_throttle_window(self):
        with TemporaryDirectory() as directory:
            runner = IndexJobRunner(
                "progress-test",
                Path(directory) / "index.db",
                Path(directory),
                Path(directory) / "state",
                True,
                Path(directory) / "customers.db",
            )

            with patch.object(runner, "_write") as write:
                runner._progress(100, "erste.xls")
                runner._progress(100, "zweite.doc")

            self.assertEqual(write.call_count, 2)
            self.assertEqual(write.call_args.kwargs["current_path"], "zweite.doc")

    @unittest.skipUnless(sys.platform == "win32", "Windows-spezifische Prozessflags")
    def test_controller_starts_windows_job_without_console_window(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            controller = IndexJobController(root / "index.db", root / "state")

            with patch(
                "app.gui.workers.index_job_controller.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 12345
                self.assertTrue(controller.start(source, full_rebuild=False))

            flags = popen.call_args.kwargs["creationflags"]
            self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
            self.assertFalse(flags & subprocess.DETACHED_PROCESS)


if __name__ == "__main__":
    unittest.main()
