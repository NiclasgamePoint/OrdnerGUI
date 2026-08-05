from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QCloseEvent, QFocusEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from app.core.statistics import ApplicationStatistics
from app.gui.settings_popup import ClickActivatedSpinBox, SettingsPopup


class SettingsPopupEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_spin_mouse_focus_and_guard_methods_without_built_widgets(self):
        spin = ClickActivatedSpinBox()
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(1, 1), QPointF(1, 1),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        spin.mousePressEvent(press)
        self.assertTrue(spin._wheel_adjustment_enabled)
        spin.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(spin._wheel_adjustment_enabled)
        spin.deleteLater()

        bare = SimpleNamespace(indexing=False, backups=[])
        SettingsPopup.set_indexing(bare, True)
        SettingsPopup.set_content_index_state(bare, {})
        SettingsPopup.set_backups(bare, [])
        SettingsPopup.set_backups_loading(bare)
        bare.blacklist_suggestions = []
        SettingsPopup.set_blacklist_suggestions(bare, [])
        bare.recognition_summary = {}
        bare.pending_recognition_cases = 0
        SettingsPopup.set_recognition_state(bare, None, 0)
        SettingsPopup.set_recognition_state_loading(bare)
        bare.diagnostics = None
        SettingsPopup.set_diagnostics(bare, None)
        SettingsPopup.set_diagnostics_loading(bare)

    def test_index_backup_content_statistics_blacklist_and_diagnostics_states(self):
        popup = SettingsPopup("invalid", "invalid", statistics=ApplicationStatistics())
        popup.request_reindex()
        popup.request_reindex()
        popup.set_index_progress(2, "file.pdf")
        popup.set_indexing(False)
        popup.set_content_index_state({
            "status": "error", "completed_documents": 1, "total_documents": 2,
            "pending_documents": 1, "failed_count": 1, "error": "bad",
        })
        self.assertIn("bad", popup.content_index_summary_label.text())
        popup.set_content_index_state({})

        emitted = []
        popup.loadBackupRequested.connect(emitted.append)
        popup.set_backups([{"label": "Backup", "path": "/backup"}])
        popup.load_selected_backup()
        self.assertEqual(emitted, ["/backup"])
        popup.set_backups([])
        popup.load_selected_backup()
        popup.set_backups_loading()

        popup.set_statistics(None)
        popup.set_statistics(None, "bad")
        popup.set_statistics(ApplicationStatistics(customer_count=1))
        popup.set_statistics_loading()
        popup.save_customer_recognition_options()

        popup.set_blacklist_suggestions([{
            "id": 1, "value_type": "custom", "value": "value",
            "folder_count": 2, "example_sources": ["one"],
        }])
        popup._confirm_blacklist_suggestion()
        popup._dismiss_blacklist_suggestion()
        popup.blacklist_suggestion_list.setCurrentRow(0)
        confirmed, dismissed = [], []
        popup.blacklistSuggestionConfirmed.connect(lambda *args: confirmed.append(args))
        popup.blacklistSuggestionDismissed.connect(dismissed.append)
        popup._confirm_blacklist_suggestion()
        popup._dismiss_blacklist_suggestion()
        self.assertTrue(confirmed)
        self.assertEqual(dismissed, [1])

        diagnostics = SimpleNamespace(
            integrity="ok", root_path="", built_at="", build_mode="",
            duration_seconds=1.2, file_count=1, folder_count=2, changed_count=3,
            content_count=4, database_size=1024, content_database_size=2048,
            shard_count=1, status_counts={"unknown": 1, "success": 2},
            errors=[{"path": "/x", "error": "bad"}],
        )
        popup.set_diagnostics(None)
        popup.set_diagnostics(diagnostics)
        diagnostics.errors = []
        popup.set_diagnostics(diagnostics)
        popup.set_diagnostics_loading()
        self.assertIn("Indexdiagnose", popup.diagnostics_text.toPlainText())
        diagnostics_page = popup._build_diagnostics_page()
        diagnostics_page.deleteLater()

        popup.show()
        popup.max_file_size_spin.setValue(popup.index_options.max_file_size_mb + 1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
                patch("app.gui.settings_popup.persist_index_options") as persist:
            popup.save_index_options()
        persist.assert_not_called()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                patch(
                    "app.gui.settings_popup.persist_index_options",
                    side_effect=OSError("readonly"),
                ), patch.object(QMessageBox, "warning") as warning:
            popup.save_index_options()
        self.assertIn("readonly", warning.call_args.args[-1])
        popup.close()

    def test_paths_modal_accent_close_and_parent_sizing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            parent = QWidget()
            parent.resize(700, 520)
            popup = SettingsPopup("light", "#2db89d", parent=parent)
            self.assertLessEqual(popup.size_for_parent().width(), 820)
            unowned = SettingsPopup("light", "#2db89d")
            self.assertEqual(unowned.size_for_parent().width(), 780)

            popup.data_path_input.clear()
            popup.apply_data_path()
            self.assertIn("auswählen", popup.path_error_label.text())
            popup.data_path_input.setText(str(root / "missing"))
            popup.apply_data_path()
            self.assertIn("existiert", popup.path_error_label.text())
            popup._clear_path_error()
            popup.data_path_input.setText(str(root))
            changed = []
            popup.dataPathChanged.connect(changed.append)
            popup.apply_data_path()
            self.assertEqual(changed, [str(root.resolve())])

            popup.show()
            with patch("app.gui.settings_popup.QFileDialog.getExistingDirectory", return_value=""):
                popup.choose_data_path()
            with patch("app.gui.settings_popup.QFileDialog.getExistingDirectory", return_value=str(root)):
                popup.choose_data_path()
            self.assertEqual(popup.data_path_input.text(), str(root))
            self.assertEqual(popup.run_modal_preserving_popup(lambda: 7), 7)

            popup._set_combo_for_accent("#ffffff")
            popup._set_combo_for_accent("invalid")
            popup._color_dialog_active = True
            event = QCloseEvent()
            popup.closeEvent(event)
            self.assertFalse(event.isAccepted())
            popup._color_dialog_active = False
            popup.close()
            unowned.close()
            parent.close()


if __name__ == "__main__":
    unittest.main()
