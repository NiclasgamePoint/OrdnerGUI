from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import asdict
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.catalog_index import CatalogIndexManager
from app.core.config import IndexOptions, load_index_options, save_index_options
from app.core.content_index import ContentStateRepository, ShardRepository
from app.core.index_layout import IndexLayout
from app.core.index_job_state import state_path, write_state
from app.services.content_index_maintenance import ContentIndexMaintenance
from app.services.index_capabilities import IndexCapabilityDetector
from app.gui.settings_popup import SettingsPopup
from app.gui.workers.content_job_controller import ContentJobController
from app.services.document_text_indexer import DocumentTextIndexer


class _MemorySettings:
    values = {}

    def __init__(self, *_args):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        pass

    def status(self):
        from PySide6.QtCore import QSettings
        return QSettings.Status.NoError

    def fileName(self):
        return "memory-settings"


class IndexPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _MemorySettings.values = {}
        self.persistence = patch("app.gui.settings_popup.persist_index_options")
        self.persistence.start()
        self.addCleanup(self.persistence.stop)

    def test_new_preferences_round_trip_through_platform_settings(self):
        options = IndexOptions(
            automatic_monitoring_enabled=False,
            change_delay_seconds=30,
            daily_reconciliation_enabled=False,
            content_indexing_enabled=False,
            resource_profile="gentle",
            preferred_document_patterns="vertrag,auftrag",
            priority_documents_per_project=12,
            newest_years_first=False,
            content_search_enabled=True,
            maximum_parallel_shards=2,
        )
        with patch("app.core.config.QSettings", _MemorySettings):
            save_index_options(options)
            loaded = load_index_options()
        self.assertEqual(asdict(loaded), asdict(options))
        self.assertEqual(loaded.ocr_workers, 1)
        self.assertEqual(loaded.document_pause_seconds, 0.25)

    def test_preferences_survive_a_fresh_application_process(self):
        with TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment["XDG_CONFIG_HOME"] = directory
            save_script = (
                "from app.core.config import IndexOptions,save_index_options;"
                "save_index_options(IndexOptions(automatic_monitoring_enabled=False,"
                "daily_reconciliation_enabled=False,content_indexing_enabled=False,"
                "ocr_enabled=False,newest_years_first=False,content_search_enabled=True))"
            )
            subprocess.run(
                [sys.executable, "-c", save_script],
                cwd=Path(__file__).resolve().parents[3],
                env=environment,
                check=True,
            )
            load_script = (
                "import json;from dataclasses import asdict;"
                "from app.core.config import load_index_options;"
                "print(json.dumps(asdict(load_index_options())))"
            )
            result = subprocess.run(
                [sys.executable, "-c", load_script],
                cwd=Path(__file__).resolve().parents[3],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            loaded = json.loads(result.stdout)
            self.assertFalse(loaded["automatic_monitoring_enabled"])
            self.assertFalse(loaded["daily_reconciliation_enabled"])
            self.assertFalse(loaded["content_indexing_enabled"])
            self.assertFalse(loaded["ocr_enabled"])
            self.assertFalse(loaded["newest_years_first"])
            self.assertTrue(loaded["content_search_enabled"])

    def test_pausing_content_index_preserves_queue_candidates(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            document = source / "Angebot.pdf"
            document.write_bytes(b"pdf")
            layout = IndexLayout(base / "index")
            layout.ensure_directories()
            with CatalogIndexManager(
                layout.catalog_path,
                options=IndexOptions(content_indexing_enabled=False),
            ) as catalog:
                catalog.synchronize_directory(source, full_rebuild=True)
                eligible = catalog.conn.execute(
                    "SELECT content_eligible FROM files WHERE path=?",
                    (str(document.resolve()),),
                ).fetchone()[0]
                self.assertEqual(eligible, 1)
                with ContentStateRepository(layout.content_state_path) as state:
                    catalog.reconcile_content_state(state, ShardRepository(layout, state), [])
                    self.assertEqual(state.progress().total_documents, 1)

    def test_format_selection_controls_catalog_eligibility(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            for name in ("scan.pdf", "brief.docx", "tabelle.xlsx", "notiz.txt"):
                (source / name).write_bytes(b"content")
            with CatalogIndexManager(
                base / "catalog.db",
                options=IndexOptions(content_extensions="pdf,txt"),
            ) as catalog:
                catalog.synchronize_directory(source, full_rebuild=True)
                eligibility = dict(catalog.conn.execute(
                    "SELECT filename,content_eligible FROM files"
                ))
            self.assertEqual(eligibility, {
                "scan.pdf": 1,
                "brief.docx": 0,
                "tabelle.xlsx": 0,
                "notiz.txt": 1,
            })

    def test_capability_detector_handles_tools_and_languages(self):
        completed = subprocess.CompletedProcess(
            ["tesseract", "--list-langs"], 0, "List of available languages\ndeu\neng\n", ""
        )
        with (
            patch("app.services.index_capabilities.shutil.which") as which,
            patch("app.services.index_capabilities.subprocess.run", return_value=completed),
        ):
            which.side_effect = lambda name: f"/tools/{name}"
            capabilities = IndexCapabilityDetector().detect()
        self.assertTrue(capabilities.ocr_available)
        self.assertEqual(capabilities.ocr_languages, ("deu", "eng"))

    def test_content_maintenance_retries_and_rebuilds_derived_data(self):
        with TemporaryDirectory() as directory:
            layout = IndexLayout(Path(directory) / "index")
            layout.ensure_directories()
            with ContentStateRepository(layout.content_state_path) as state:
                state.reconcile_document(
                    document_key="doc",
                    path="/source/document.pdf",
                    source_version="v1",
                    partition_year=2026,
                    source_size=10,
                    priority=10,
                    catalog_generation="g1",
                )
                state.connection.execute(
                    "UPDATE documents SET status='failed',attempts=3,content_error='x'"
                )
                state.connection.commit()
            maintenance = ContentIndexMaintenance(layout)
            result = maintenance.retry_failed()
            self.assertEqual(result.affected_documents, 1)
            with ContentStateRepository(layout.content_state_path) as state:
                row = state.connection.execute(
                    "SELECT status,attempts,content_error FROM documents"
                ).fetchone()
                self.assertEqual(tuple(row), ("pending", 0, ""))
            result = maintenance.rebuild()
            self.assertEqual(result.affected_documents, 1)
            corrupt = layout.corrupt_shard_dir / "old-content.db"
            corrupt.write_bytes(b"derived")
            content_job_dir = layout.jobs_dir / "content"
            write_state(content_job_dir, {"status": "cancelled"})
            maintenance.clear()
            self.assertFalse(corrupt.exists())
            self.assertFalse(layout.content_state_path.exists())
            self.assertFalse(state_path(content_job_dir).exists())

    def test_queue_can_process_oldest_year_first(self):
        with TemporaryDirectory() as directory:
            layout = IndexLayout(Path(directory) / "index")
            with ContentStateRepository(layout.content_state_path) as state:
                for year in (2026, 2020):
                    state.reconcile_document(
                        document_key=str(year),
                        path=f"/{year}.pdf",
                        source_version="v1",
                        partition_year=year,
                        source_size=1,
                        priority=100,
                        catalog_generation="g1",
                    )
                state.connection.commit()
                task = state.acquire_next(newest_years_first=False)
                self.assertIsNotNone(task)
                self.assertEqual(task.partition_year, 2020)

    def test_content_pause_marker_survives_controller_restart(self):
        with TemporaryDirectory() as directory:
            layout = IndexLayout(Path(directory) / "index")
            layout.ensure_directories()
            layout.catalog_path.touch()
            controller = ContentJobController(layout, Path(directory) / "customers.db")
            controller.pause()
            with patch(
                "app.gui.workers.content_job_controller.subprocess.Popen"
            ) as popen, patch(
                "app.gui.workers.content_job_controller.open_content_process_log"
            ) as open_log:
                log_handle = Mock()
                open_log.return_value = log_handle
                self.assertFalse(controller.start_or_adopt())
                popen.assert_not_called()
                self.assertTrue(controller.resume())
                popen.assert_called_once()
                options = popen.call_args.kwargs
                self.assertIs(options["stdout"], log_handle)
                self.assertEqual(options["stderr"], subprocess.STDOUT)
                log_handle.close.assert_called_once()
            controller._timer.stop()

    def test_settings_emit_new_index_and_search_preferences(self):
        popup = SettingsPopup("light", "#2db89d")
        popup.automatic_monitoring_checkbox.setChecked(False)
        popup.content_indexing_checkbox.setChecked(False)
        popup.resource_profile_combo.setCurrentIndex(
            popup.resource_profile_combo.findData("fast")
        )
        popup.content_search_checkbox.setChecked(True)
        popup.parallel_shards_spin.setValue(6)
        received = []
        popup.indexOptionsChanged.connect(received.append)
        popup.save_index_options()
        self.assertEqual(len(received), 1)
        self.assertFalse(received[0].automatic_monitoring_enabled)
        self.assertFalse(received[0].content_indexing_enabled)
        self.assertEqual(received[0].resource_profile, "fast")
        self.assertTrue(received[0].content_search_enabled)
        self.assertEqual(received[0].maximum_parallel_shards, 6)
        popup.close()

    def test_every_index_toggle_is_transferred_to_options(self):
        popup = SettingsPopup("light", "#2db89d")
        popup.automatic_monitoring_checkbox.setChecked(False)
        popup.daily_reconciliation_checkbox.setChecked(False)
        popup.content_indexing_checkbox.setChecked(False)
        popup.newest_years_checkbox.setChecked(False)
        popup.ocr_checkbox.setChecked(False)
        for checkbox, _extensions in popup.content_format_checkboxes.values():
            checkbox.setChecked(False)

        received = []
        popup.indexOptionsChanged.connect(received.append)
        popup.save_index_options()

        options = received[0]
        self.assertFalse(options.automatic_monitoring_enabled)
        self.assertFalse(options.daily_reconciliation_enabled)
        self.assertFalse(options.content_indexing_enabled)
        self.assertFalse(options.newest_years_first)
        self.assertFalse(options.ocr_enabled)
        self.assertEqual(options.indexed_content_types, set())
        popup.close()

    def test_each_format_toggle_controls_its_extensions(self):
        popup = SettingsPopup("light", "#2db89d")
        for label, (checkbox, extensions) in popup.content_format_checkboxes.items():
            with self.subTest(label=label):
                for other, _values in popup.content_format_checkboxes.values():
                    other.setChecked(False)
                checkbox.setChecked(True)
                received = []
                popup.indexOptionsChanged.connect(received.append)
                popup.save_index_options()
                self.assertEqual(received[-1].indexed_content_types, extensions)
        popup.close()

    def test_modal_confirmation_keeps_popup_alive_and_emits_changes(self):
        popup = SettingsPopup("light", "#2db89d")
        popup.show()
        self.app.processEvents()
        popup.ocr_checkbox.setChecked(not popup.ocr_checkbox.isChecked())
        received = []
        popup.indexOptionsChanged.connect(received.append)

        def close_popup_then_confirm(*_args, **_kwargs):
            popup.close()
            return QMessageBox.StandardButton.Yes

        with patch(
            "app.gui.settings_popup.QMessageBox.question",
            side_effect=close_popup_then_confirm,
        ):
            popup.save_index_options_button.click()
        self.app.processEvents()

        self.assertEqual(len(received), 1)
        self.assertTrue(popup.isVisible())
        popup.close()

    def test_excel_exclusion_from_visible_popup_survives_fresh_process(self):
        self.persistence.stop()
        with TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment["XDG_CONFIG_HOME"] = directory
            environment["QT_QPA_PLATFORM"] = "offscreen"
            save_script = "\n".join((
                "from PySide6.QtCore import QTimer",
                "from PySide6.QtWidgets import QApplication, QMessageBox",
                "from app.gui.settings_popup import SettingsPopup",
                "app = QApplication([])",
                "popup = SettingsPopup('light', '#2db89d')",
                "popup.show()",
                "app.processEvents()",
                "popup.content_format_checkboxes['Excel (XLS/XLSX)'][0].setChecked(False)",
                "def confirm():",
                "    for widget in QApplication.topLevelWidgets():",
                "        if isinstance(widget, QMessageBox):",
                "            widget.button(QMessageBox.StandardButton.Yes).click()",
                "QTimer.singleShot(25, confirm)",
                "popup.save_index_options_button.click()",
                "app.processEvents()",
            ))
            subprocess.run(
                [sys.executable, "-c", save_script],
                cwd=Path(__file__).resolve().parents[3],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            load_script = (
                "import json;from app.core.config import load_index_options;"
                "print(json.dumps(sorted(load_index_options().indexed_content_types)))"
            )
            result = subprocess.run(
                [sys.executable, "-c", load_script],
                cwd=Path(__file__).resolve().parents[3],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            extensions = json.loads(result.stdout)
            self.assertNotIn("xls", extensions)
            self.assertNotIn("xlsx", extensions)

    def test_ocr_toggle_prevents_fallback_for_image_only_pdf(self):
        disabled = DocumentTextIndexer(IndexOptions(ocr_enabled=False))
        with (
            patch.object(disabled.tools, "resolve", return_value=None),
            patch.object(
                disabled, "_run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            patch.object(disabled, "_ocr_pdf", return_value="OCR") as ocr,
        ):
            self.assertEqual(disabled._pdf(Path("scan.pdf")), "")
            ocr.assert_not_called()

        enabled = DocumentTextIndexer(IndexOptions(ocr_enabled=True))
        with (
            patch.object(enabled.tools, "resolve", return_value=None),
            patch.object(
                enabled, "_run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            patch.object(enabled, "_ocr_pdf", return_value=("OCR", 1, 1)) as ocr,
        ):
            self.assertEqual(enabled._pdf(Path("scan.pdf")), "OCR")
            ocr.assert_called_once()

    def test_settings_content_status_updates_for_running_and_terminal_states(self):
        popup = SettingsPopup("light", "#2db89d")
        popup.set_content_index_state({
            "status": "running",
            "completed_documents": 25,
            "total_documents": 100,
            "pending_documents": 74,
            "failed_documents": 1,
        })
        self.assertIn("läuft", popup.content_index_summary_label.text())
        self.assertIn("25/100 (25 %)", popup.content_index_summary_label.text())
        popup.set_content_index_state({
            "status": "completed",
            "completed_documents": 100,
            "total_documents": 100,
            "pending_documents": 0,
        })
        self.assertIn("abgeschlossen", popup.content_index_summary_label.text())
        self.assertIn("100/100 (100 %)", popup.content_index_summary_label.text())
        popup.close()


if __name__ == "__main__":
    unittest.main()
