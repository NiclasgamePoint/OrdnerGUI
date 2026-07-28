from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtWidgets import QApplication

from app.core.index_job_state import write_state
from app.gui.index_tray_window import IndexTrayWindow


class IndexTrayWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_displays_current_state_and_last_five_log_lines(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log_file = root / "papagui.log"
            log_file.write_text(
                "\n".join(f"Zeile {number}" for number in range(1, 8)) + "\n",
                encoding="utf-8",
            )
            write_state(root, {
                "status": "running",
                "processed_count": 42,
                "current_path": str(root / "beispiel.pdf"),
            })

            window = IndexTrayWindow(root, log_file)
            window.refresh()

            self.assertIn("läuft", window.status_label.text())
            self.assertIn("42 Dateien", window.detail_label.text())
            self.assertIn("beispiel.pdf", window.detail_label.text())
            self.assertNotIn("Zeile 2\n", window.log_output.toPlainText())
            self.assertIn("Zeile 3", window.log_output.toPlainText())
            self.assertIn("Zeile 7", window.log_output.toPlainText())
            self.assertFalse(window.cancel_button.isHidden())
            window.close()


if __name__ == "__main__":
    unittest.main()
