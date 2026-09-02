from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

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

    def test_displays_background_file_index_progress(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_state_dir = root / "catalog"
            content_state_dir = root / "content"
            current_file = root / "angebot.pdf"
            write_state(catalog_state_dir, {
                "status": "completed", "indexed_count": 80
            })
            write_state(content_state_dir, {
                "status": "running",
                "completed_documents": 30,
                "total_documents": 120,
                "pending_documents": 90,
                "failed_documents": 2,
                "current_path": str(current_file),
                "active_workers": 2,
                "worker_limit": 4,
                "resource_profile": "balanced",
                "worker_assignments": [
                    {"worker": 1, "path": str(root / "angebot.pdf")},
                    {"worker": 3, "path": str(root / "vertrag.docx")},
                ],
            })

            window = IndexTrayWindow(
                catalog_state_dir,
                root / "missing.log",
                content_state_dir=content_state_dir,
            )
            window.refresh()

            self.assertIn("Dokumentinhalte", window.content_status_label.text())
            self.assertEqual(window.content_progress_bar.maximum(), 120)
            self.assertEqual(window.content_progress_bar.value(), 30)
            self.assertIn("30 von 120", window.content_detail_label.text())
            self.assertIn("90 ausstehend", window.content_detail_label.text())
            self.assertIn("2 Dokumente mit Fehlern", window.content_detail_label.text())
            self.assertIn("2 von 4 Workern aktiv", window.worker_summary_label.text())
            self.assertIn("Profil Ausgewogen", window.worker_summary_label.text())
            self.assertIn("Worker 1:", window.worker_output.toPlainText())
            self.assertIn("angebot.pdf", window.worker_output.toPlainText())
            self.assertIn("Worker 3:", window.worker_output.toPlainText())
            self.assertIn("vertrag.docx", window.worker_output.toPlainText())
            self.assertFalse(window.cancel_button.isHidden())
            window.close()

    def test_running_legacy_job_does_not_claim_zero_active_workers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_state_dir = root / "catalog"
            content_state_dir = root / "content"
            current_file = root / "legacy-running.pdf"
            write_state(catalog_state_dir, {"status": "completed"})
            write_state(content_state_dir, {
                "status": "running",
                "worker_limit": 9,
                "current_path": str(current_file),
                "completed_documents": 10,
                "total_documents": 20,
            })
            window = IndexTrayWindow(
                catalog_state_dir,
                root / "missing.log",
                content_state_dir=content_state_dir,
            )
            window.refresh()
            self.assertIn("Index arbeitet", window.worker_summary_label.text())
            self.assertNotIn("0 von 9", window.worker_summary_label.text())
            self.assertIn("legacy-running.pdf", window.worker_output.toPlainText())
            window.close()

    def test_server_badge_colors_and_animates_live_states(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            window = IndexTrayWindow(root / "catalog", server_url="http://server")
            payload = {
                "server": {"status": "online", "interval_seconds": 3600},
                "job": {"status": "completed"},
                "content": {"status": "completed"},
            }
            window._apply_status(payload)
            self.assertIn("#27a989", window.server_badge.text())
            self.assertIn("ONLINE", window.server_badge.text())

            payload["job"] = {"status": "running"}
            window._apply_status(payload)
            self.assertIn("INDEXLAUF", window.server_badge.text())
            self.assertTrue(window.server_badge._animation.isActive())

            payload["job"] = {"status": "error"}
            window._apply_status(payload)
            self.assertIn("#df9328", window.server_badge.text())
            self.assertIn("PROBLEM", window.server_badge.text())

            window._error("status", "nicht erreichbar")
            self.assertIn("#d95c5c", window.server_badge.text())
            self.assertIn("OFFLINE", window.server_badge.text())
            window.deleteLater()

    def test_interval_editor_is_protected_from_refresh_and_queues_save(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            window = IndexTrayWindow(root / "catalog", server_url="http://server")
            window._set_interval(86_400)
            self.assertEqual(window.interval_value.value(), 24)
            self.assertEqual(window.interval_unit.currentData(), 3600)

            window.interval_value.setValue(12)
            window._apply_status({
                "server": {"status": "online", "interval_seconds": 3600},
                "job": {},
                "content": {},
            })
            self.assertEqual(window.interval_value.value(), 12)

            busy_worker = Mock()
            busy_worker.isRunning.return_value = True
            window._worker = busy_worker
            window._save_settings()
            operation, values = window._pending_operation
            self.assertEqual(operation, "save_settings")
            self.assertEqual(values["interval_seconds"], 43_200)
            window._worker = None
            window.deleteLater()

    def test_status_edges_worker_payloads_and_window_lifecycle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog"
            content = root / "content"
            window = IndexTrayWindow(catalog, root / "missing.log", content_state_dir=content)

            write_state(catalog, {"status": "error", "error": "catalog bad"})
            write_state(content, {
                "status": "starting", "failed_count": 1,
                "worker_assignments": "invalid", "worker_limit": 2,
                "error": "content bad",
            })
            window.refresh()
            self.assertIn("catalog bad", window.detail_label.text())
            self.assertIn("1 Dokumente", window.content_detail_label.text())
            self.assertIn("content bad", window.content_detail_label.text())

            write_state(catalog, {"status": "no_changes"})
            write_state(content, {
                "status": "completed", "completed_documents": 5,
                "total_documents": 3, "worker_assignments": [None, {"worker": 2}],
            })
            window.refresh()
            self.assertEqual(window.progress_bar.value(), 100)
            self.assertEqual(window.content_progress_bar.value(), 3)

            with patch.object(window, "refresh") as refresh, \
                    patch.object(window, "show") as show, \
                    patch.object(window, "raise_") as raise_window, \
                    patch.object(window, "activateWindow") as activate:
                window.show_status()
            refresh.assert_called_once()
            show.assert_called_once()
            raise_window.assert_called_once()
            activate.assert_called_once()
            self.assertTrue(window.timer.isActive())
            event = Mock()
            window.closeEvent(event)
            event.ignore.assert_called_once()
            self.assertFalse(window.timer.isActive())
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
