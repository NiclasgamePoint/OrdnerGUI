from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import index_job
from app.services.index_job import IndexJobRunner
from tests.base.test_case import PapaGuiTestCase


class IndexJobEdgeTests(PapaGuiTestCase):
    def runner(self, catalog: bool = False) -> IndexJobRunner:
        active = (
            self.temp_path / "index" / "catalog" / "active.db"
            if catalog
            else self.temp_path / "active.db"
        )
        active.parent.mkdir(parents=True, exist_ok=True)
        source = self.temp_path / "source"
        source.mkdir(exist_ok=True)
        return IndexJobRunner(
            "job", active, source, self.temp_path / "state", False,
            self.temp_path / "customers.db",
        )

    def test_legacy_run_no_changes_interruption_error_and_final_close(self):
        runner = self.runner()
        runner.active_path.touch()
        manager = Mock(last_change_count=0)
        manager.synchronize_directory.return_value = 5
        build = self.temp_path / "build.db"
        build.touch()
        with (
            patch("app.services.index_job.create_build_path", return_value=build),
            patch("app.services.index_job.seed_build_database"),
            patch("app.services.index_job.IndexManager", return_value=manager),
            patch("app.services.index_job.validate_index"),
            patch.object(runner, "_recognize_customers", return_value={}),
            patch.object(runner, "_write") as write,
        ):
            self.assertEqual(runner.run(), 0)
        self.assertEqual(write.call_args.kwargs["status"], "no_changes")
        self.assertFalse(build.exists())

        manager = Mock()
        manager.synchronize_directory.side_effect = InterruptedError
        build.touch()
        with (
            patch("app.services.index_job.create_build_path", return_value=build),
            patch("app.services.index_job.seed_build_database"),
            patch("app.services.index_job.IndexManager", return_value=manager),
            patch.object(runner, "_write") as write,
        ):
            self.assertEqual(runner.run(), 2)
        self.assertEqual(write.call_args.kwargs["status"], "cancelled")

        manager = Mock()
        manager.synchronize_directory.side_effect = RuntimeError("bad")
        build.touch()
        with (
            patch("app.services.index_job.create_build_path", return_value=build),
            patch("app.services.index_job.seed_build_database"),
            patch("app.services.index_job.IndexManager", return_value=manager),
            patch.object(runner, "_write") as write,
        ):
            self.assertEqual(runner.run(), 1)
        self.assertEqual(write.call_args.kwargs, {"status": "error", "error": "bad"})
        manager.close.assert_called_once_with()

    def test_progress_cancellation_and_activation_wait_paths(self):
        runner = self.runner()
        with patch.object(runner, "_write") as write, patch("app.services.index_job.time.monotonic", return_value=1):
            runner._progress(20, "same")
            runner._progress(20, "same")
            runner._progress(3, "same")
        self.assertEqual(write.call_count, 2)
        cancel = runner.state_dir / "index_job.cancel"
        cancel.parent.mkdir(parents=True)
        self.assertFalse(runner._cancel_requested())
        cancel.touch()
        self.assertTrue(runner._cancel_requested())
        cancel.unlink()

        build = self.temp_path / "ready.db"
        build.touch()
        runner.build_path = build
        activated = runner.state_dir / "index_job.activated"
        activated.touch()
        with patch.object(runner, "_after_catalog_activation", return_value={}), patch.object(runner, "_write") as write:
            self.assertEqual(runner._wait_for_activation(3, 2), 0)
        self.assertEqual(write.call_args.kwargs["activated_by"], "gui")
        activated.unlink()

        cancel.touch()
        with patch.object(runner, "_write") as write:
            self.assertEqual(runner._wait_for_activation(3, 2), 2)
        self.assertEqual(write.call_args.kwargs["status"], "cancelled")
        cancel.unlink()

        runner.build_path = build
        build.touch()
        with (
            patch("app.services.index_job.read_owner", return_value=123),
            patch("app.services.index_job.process_is_alive", side_effect=[True, False, False]),
            patch("app.services.index_job.time.monotonic", side_effect=[1, 1, 3]),
            patch("app.services.index_job.time.sleep"),
            patch.object(runner, "_activate_build") as activate,
            patch.object(runner, "_after_catalog_activation", return_value={}),
            patch.object(runner, "_write") as write,
        ):
            self.assertEqual(runner._wait_for_activation(3, 2), 0)
        activate.assert_called_once_with()
        self.assertEqual(write.call_args.kwargs["activated_by"], "worker")

    def test_recognition_activation_and_queue_helpers(self):
        runner = self.runner()
        service = Mock()
        service.synchronize.return_value = SimpleNamespace(
            detected=1, created=2, assigned=3, skipped=4, pending=5, error="",
        )
        with patch("app.services.index_job.CustomerRecognitionService", return_value=service):
            result = runner._recognize_customers(Path("index"))
        self.assertEqual(result["customers_created"], 2)
        with patch("app.services.index_job.CustomerRecognitionService", side_effect=RuntimeError("bad")):
            self.assertEqual(runner._recognize_customers(Path("index"))["customer_sync_error"], "bad")
        with self.assertRaises(RuntimeError):
            runner._activate_build()
        runner.build_path = self.temp_path / "build"
        with patch("app.services.index_job.activate_index") as activate:
            runner._activate_build()
        activate.assert_called_once()
        with patch.object(runner, "_recognize_customers", return_value={"ok": True}):
            self.assertEqual(runner._after_catalog_activation(start_content=True), {"ok": True})
        runner._reconcile_content_queue(Path("catalog"))

        catalog_runner = self.runner(catalog=True)
        catalog_runner.build_path = self.temp_path / "catalog-build"
        catalog_runner._catalog_store = Mock()
        catalog_runner._activate_build()
        catalog_runner._catalog_store.activate.assert_called_once_with(catalog_runner.build_path)
        with (
            patch.object(catalog_runner, "_reconcile_content_queue") as reconcile,
            patch.object(catalog_runner, "_recognize_customers", return_value={}),
            patch.object(catalog_runner, "_archive_legacy_index") as archive,
            patch.object(catalog_runner, "_start_content_job") as start,
        ):
            catalog_runner._after_catalog_activation(start_content=True)
        reconcile.assert_called_once()
        archive.assert_called_once()
        start.assert_called_once()
        with (
            patch.object(catalog_runner, "_reconcile_content_queue") as reconcile,
            patch.object(catalog_runner, "_recognize_customers", return_value={}),
            patch.object(catalog_runner, "_archive_legacy_index"),
            patch.object(catalog_runner, "_start_content_job") as start,
        ):
            catalog_runner._after_catalog_activation(start_content=False)
        reconcile.assert_called_once()
        start.assert_not_called()

        manager = Mock()
        state = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=state)
        context.__exit__ = Mock(return_value=False)
        with (
            patch("app.services.index_job.CatalogIndexManager", return_value=manager),
            patch("app.services.index_job.ContentStateRepository.open_recoverable", return_value=context),
            patch("app.services.index_job.load_index_options", return_value=SimpleNamespace(
                preferred_patterns=[], priority_documents_per_project=1, newest_years_first=True,
            )),
        ):
            catalog_runner._reconcile_content_queue(Path("catalog"))
        manager.close.assert_called_once_with()

    def test_content_job_start_guards_process_options_and_log_cleanup(self):
        runner = self.runner()
        runner._start_content_job()
        runner = self.runner(catalog=True)
        state_dir = runner.index_layout.jobs_dir / "content"
        state_dir.mkdir(parents=True)
        pause = state_dir / "index_job.paused"
        pause.touch()
        runner._start_content_job()
        pause.unlink()
        with patch("app.services.index_job.load_index_options", return_value=SimpleNamespace(content_indexing_enabled=False)):
            runner._start_content_job()
        with (
            patch("app.services.index_job.load_index_options", return_value=SimpleNamespace(content_indexing_enabled=True)),
            patch("app.services.index_job.read_state", return_value={"status": "running", "pid": 2}),
            patch("app.services.index_job.process_is_alive", return_value=True),
        ):
            runner._start_content_job()

        stream = Mock()
        for platform_name in ("linux", "win32"):
            with (
                patch("app.services.index_job.load_index_options", return_value=SimpleNamespace(content_indexing_enabled=True)),
                patch("app.services.index_job.read_state", return_value={}),
                patch("app.services.index_job.sys.platform", platform_name),
                patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 1, create=True),
                patch.object(subprocess, "CREATE_NO_WINDOW", 2, create=True),
                patch("app.services.index_job.open_content_process_log", return_value=stream),
                patch("app.services.index_job.subprocess.Popen") as popen,
            ):
                runner._start_content_job()
            self.assertIn("creationflags" if platform_name == "win32" else "start_new_session", popen.call_args.kwargs)
        self.assertEqual(stream.close.call_count, 2)

    def test_archive_cleanup_parser_main_and_state_write(self):
        self.runner()._archive_legacy_index()
        runner = self.runner(catalog=True)
        runner.index_layout.catalog_path.touch()
        legacy = runner.index_layout.root.parent / "index.db"
        legacy.write_text("legacy", encoding="utf-8")
        backup = legacy.parent / "index.backup.1.db"
        backup.write_text("backup", encoding="utf-8")
        broken = legacy.parent / "index.backup.broken.db"
        broken.symlink_to(legacy.parent / "missing-target")
        runner.index_layout.legacy_dir.mkdir(parents=True, exist_ok=True)
        (runner.index_layout.legacy_dir / "index.db").write_text("existing", encoding="utf-8")
        runner._archive_legacy_index()
        self.assertTrue((runner.index_layout.legacy_dir / "index.1.db").exists())
        self.assertTrue((runner.index_layout.legacy_dir / backup.name).exists())

        build = self.temp_path / "build"
        build.touch()
        runner.build_path = build
        runner._cleanup_build()
        self.assertIsNone(runner.build_path)
        runner._cleanup_build()
        with patch("app.services.index_job.write_state") as write:
            runner._write(status="ok")
        self.assertEqual(write.call_args.args[1]["job_id"], "job")

        parsed = index_job.build_parser().parse_args([
            "--job-id", "id", "--active", "active", "--source", "source",
            "--state-dir", "state", "--customers", "customers", "--full-rebuild",
        ])
        self.assertTrue(parsed.full_rebuild)
        with (
            patch.object(sys, "argv", ["job", "--job-id", "id", "--active", "active", "--source", "source", "--state-dir", "state", "--customers", "customers"]),
            patch.object(index_job, "suppress_windows_crash_dialogs") as suppress,
            patch.object(index_job, "configure_logging") as logging,
            patch.object(index_job.IndexJobRunner, "run", return_value=7),
        ):
            self.assertEqual(index_job.main(), 7)
        suppress.assert_called_once_with()
        logging.assert_called_once_with()

    def test_catalog_no_change_run_reconciles_and_starts_content(self):
        runner = self.runner(catalog=True)
        runner.active_path.touch()
        build = self.temp_path / "catalog-build.db"
        build.touch()
        runner._catalog_store = Mock()
        runner._catalog_store.create_build_path.return_value = build
        manager = Mock(last_change_count=0)
        manager.synchronize_directory.return_value = 4
        with (
            patch("app.services.index_job.CatalogIndexManager", return_value=manager),
            patch.object(runner, "_recognize_customers", return_value={}),
            patch.object(runner, "_reconcile_content_queue") as reconcile,
            patch.object(runner, "_start_content_job") as start,
        ):
            self.assertEqual(runner.run(), 0)
        reconcile.assert_called_once_with(runner.active_path)
        start.assert_called_once_with()
