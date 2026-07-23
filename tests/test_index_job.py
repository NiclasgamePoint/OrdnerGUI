from pathlib import Path
from tempfile import TemporaryDirectory
import os
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.core.index_job_state import read_state, write_state
from app.core.config import CustomerRecognitionOptions
from app.core.customer_repository import CustomerRepository
from app.core.index_manager import IndexManager
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

    def test_detached_runner_recognizes_customers_after_activation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = source / "DEKRA" / "2026" / "Müller, Berlin"
            project.mkdir(parents=True)
            (project / "eins.txt").write_text("Kundendokument", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
