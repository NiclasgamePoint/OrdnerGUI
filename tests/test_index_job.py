from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from PySide6.QtWidgets import QApplication

from app.core.index_job_state import read_state
from app.core.index_manager import IndexManager
from app.gui.workers.index_job_controller import IndexJobController


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


if __name__ == "__main__":
    unittest.main()
