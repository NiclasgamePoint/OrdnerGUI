from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtWidgets import QApplication, QDialog

from app.gui.dialogs.onboarding import OnboardingDialog


class OnboardingDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_accepts_reachable_directory(self):
        with TemporaryDirectory() as directory:
            dialog = OnboardingDialog()
            dialog.path_input.setText(directory)
            dialog.accept_path()

            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertEqual(dialog.selected_path, Path(directory).resolve())

    def test_rejects_unreachable_network_path(self):
        dialog = OnboardingDialog()
        dialog.path_input.setText("/definitely/not/a/reachable/share")
        dialog.accept_path()

        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        self.assertIn("nicht erreichbar", dialog.error_label.text())


if __name__ == "__main__":
    unittest.main()
