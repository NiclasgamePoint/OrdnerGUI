from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from app.core.index_layout import IndexLayout
from app.core.content_index import ContentStateRepository
from app.services.extraction_models import ExtractionResult
from app.services import content_index_job
from app.services.content_index_job import ContentIndexJobRunner
from app.services.content_index_maintenance import ContentIndexMaintenance
from app.services.content_index_worker import ContentIndexWorker
from app.services.content_index_worker import ContentWorkerResult
from tests.base.test_case import PapaGuiTestCase


class ContentServiceEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.layout = IndexLayout(self.temp_path / "index")
        self.layout.ensure_directories()

    def test_maintenance_optimize_clear_empty_and_filters_shard_files(self):
        maintenance = ContentIndexMaintenance(self.layout)
        state = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=state)
        context.__exit__ = Mock(return_value=False)
        service = Mock()
        service.maintain.return_value = {"optimized": 1, "compacted": 2, "requeued": 3}
        with (
            patch("app.services.content_index_maintenance.ContentStateRepository.open_recoverable", return_value=context),
            patch("app.services.content_index_maintenance.ShardMaintenanceService", return_value=service),
        ):
            result = maintenance.optimize()
        self.assertEqual((result.optimized_shards, result.compacted_shards, result.requeued_documents), (1, 2, 3))

        removable = ["one.db", "one.db-wal", "one.db-shm"]
        for name in removable + ["keep.txt"]:
            (self.layout.shard_dir / name).write_text("x", encoding="utf-8")
        (self.layout.corrupt_shard_dir / "nested").mkdir()
        (self.layout.corrupt_shard_dir / "old.db").write_text("x", encoding="utf-8")
        result = maintenance.clear()
        self.assertEqual(result.affected_documents, 0)
        for name in removable:
            self.assertFalse((self.layout.shard_dir / name).exists())
        self.assertTrue((self.layout.shard_dir / "keep.txt").exists())
        self.assertFalse((self.layout.corrupt_shard_dir / "old.db").exists())
        maintenance._remove_shard_files()
        with patch.object(Path, "exists", return_value=False):
            maintenance._remove_shard_files()
        empty_layout = IndexLayout(self.temp_path / "empty-index")
        ContentIndexMaintenance(empty_layout).clear()

    def test_worker_helpers_cover_activity_resource_policy_and_logging(self):
        extractor = Mock()
        worker = ContentIndexWorker(
            self.layout,
            extractor,
            shard_target_bytes=100,
            maximum_workers=50,
            pause_seconds=-1,
        )
        self.assertEqual(worker.maximum_workers, 20)
        self.assertEqual(worker.pause_seconds, 0)
        self.assertEqual(worker._current_limit(), 20)
        policy = Mock()
        policy.worker_limit.return_value = 2
        worker.resource_policy = policy
        self.assertEqual(worker._current_limit(), 2)
        ContentIndexWorker._report_activity({}, None)
        callback = Mock()
        task1 = SimpleNamespace(path="b")
        task2 = SimpleNamespace(path="a")
        ContentIndexWorker._report_activity({Mock(): (task1, 2), Mock(): (task2, 1)}, callback)
        self.assertEqual(callback.call_args.args[0], [{"worker": 1, "path": "a"}, {"worker": 2, "path": "b"}])

    def test_worker_pause_extractor_exception_and_retryable_failure(self):
        document = self.temp_path / "document.txt"
        document.write_text("text", encoding="utf-8")

        def queue_document(key: str):
            with ContentStateRepository(self.layout.content_state_path) as state:
                state.reconcile_document(
                    document_key=key,
                    path=str(document),
                    source_version=key,
                    partition_year=2026,
                    source_size=document.stat().st_size,
                    priority=1,
                    catalog_generation="generation",
                )
                state.connection.commit()

        queue_document("exception")
        extractor = Mock()
        extractor.extract.side_effect = RuntimeError("broken")
        worker = ContentIndexWorker(
            self.layout, extractor, shard_target_bytes=1000,
            maximum_attempts=3, pause_seconds=0.01,
        )
        with patch("app.services.content_index_worker.time.sleep") as sleep:
            result = worker.run()
        self.assertEqual(sleep.call_args_list, [call(0.01)] * 3)
        self.assertEqual(result.failed, 1)

        queue_document("timeout")
        extractor.extract.side_effect = None
        extractor.extract.return_value = ExtractionResult(status="timeout", error="slow")
        result = worker.run()
        self.assertEqual(result.failed, 1)

    def test_content_job_parser_main_progress_activity_and_error(self):
        parser = content_index_job.build_parser()
        arguments = parser.parse_args([
            "--index-root", "index", "--state-dir", "state", "--customers", "customers",
        ])
        self.assertEqual(arguments.index_root, Path("index"))

        runner = ContentIndexJobRunner(self.layout, self.temp_path / "state", self.temp_path / "customers.db")
        runner._job_state = {"job_id": "id"}
        with patch("app.services.content_index_job.write_state") as write:
            runner._activity([{"worker": 1}])
        self.assertEqual(runner._job_state["active_workers"], 1)
        write.assert_called_once()
        progress = SimpleNamespace(
            total_documents=5, completed_documents=2, pending_documents=2,
            failed_documents=1, total_bytes=10, completed_bytes=4, complete=False,
        )
        with patch.object(runner, "_write") as write:
            runner._progress(progress, "/file")
        self.assertEqual(write.call_args.kwargs["completed_documents"], 2)

        with (
            patch.object(sys, "argv", ["job", "--index-root", "index", "--state-dir", "state", "--customers", "customers"]),
            patch.object(content_index_job, "suppress_windows_crash_dialogs") as suppress,
            patch.object(content_index_job, "configure_logging") as logging,
            patch.object(content_index_job.ContentIndexJobRunner, "run", return_value=7),
        ):
            self.assertEqual(content_index_job.main(), 7)
        suppress.assert_called_once_with()
        logging.assert_called_once()

        with (
            patch.object(runner, "_reconcile_queue", side_effect=RuntimeError("broken")),
            patch.object(runner, "_write") as write,
        ):
            self.assertEqual(runner.run(), 1)
        self.assertEqual(write.call_args.kwargs, {"status": "error", "error": "broken"})

    def test_content_job_reconcile_always_closes_and_customer_enrichment(self):
        runner = ContentIndexJobRunner(self.layout, self.temp_path / "state", self.temp_path / "customers.db")
        manager = Mock()
        state = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=state)
        context.__exit__ = Mock(return_value=False)
        with (
            patch("app.services.content_index_job.CatalogIndexManager", return_value=manager),
            patch("app.services.content_index_job.ContentStateRepository.open_recoverable", return_value=context),
            patch("app.services.content_index_job.load_index_options", return_value=SimpleNamespace(
                preferred_patterns=(), priority_documents_per_project=1, newest_years_first=True,
            )),
        ):
            runner._reconcile_queue()
        manager.reconcile_content_state.assert_called_once()
        manager.close.assert_called_once_with()

        service = Mock()
        with (
            patch("app.services.content_index_job.CustomerRecognitionService", return_value=service),
            patch("app.services.content_index_job.load_customer_recognition_options", return_value=object()),
        ):
            runner._enrich_customers()
        service.synchronize.assert_called_once_with(include_documents=True)

    def test_content_job_cancelled_priority_and_requeued_maintenance_pass(self):
        progress = SimpleNamespace(
            total_documents=2, completed_documents=1, pending_documents=0,
            failed_documents=1, total_bytes=2, completed_bytes=1, complete=False,
        )
        runner = ContentIndexJobRunner(self.layout, self.temp_path / "state", self.temp_path / "customers.db")
        options = SimpleNamespace(
            resource_profile="balanced", document_pause_seconds=0,
            newest_years_first=True,
        )
        worker = Mock()
        worker.run.return_value = ContentWorkerResult(1, 0, True, progress)
        with (
            patch.object(runner, "_reconcile_queue"),
            patch("app.services.content_index_job.load_index_options", return_value=options),
            patch("app.services.content_index_job.IndexResourcePolicy") as policy,
            patch("app.services.content_index_job.ContentIndexWorker", return_value=worker),
        ):
            policy.return_value.worker_limit.return_value = 1
            self.assertEqual(runner.run(), 2)

        results = [
            ContentWorkerResult(1, 0, False, progress),
            ContentWorkerResult(1, 0, False, progress),
            ContentWorkerResult(1, 1, False, progress),
        ]
        worker.reset_mock()
        worker.run.side_effect = results
        state = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=state)
        context.__exit__ = Mock(return_value=False)
        maintenance = Mock()
        maintenance.maintain.return_value = {"requeued": 1}
        with (
            patch.object(runner, "_reconcile_queue"),
            patch.object(runner, "_enrich_customers"),
            patch("app.services.content_index_job.load_index_options", return_value=options),
            patch("app.services.content_index_job.IndexResourcePolicy") as policy,
            patch("app.services.content_index_job.ContentIndexWorker", return_value=worker),
            patch("app.services.content_index_job.ContentStateRepository.open_recoverable", return_value=context),
            patch("app.services.content_index_job.ShardMaintenanceService", return_value=maintenance),
        ):
            policy.return_value.worker_limit.return_value = 1
            self.assertEqual(runner.run(), 0)
        self.assertEqual(worker.run.call_count, 3)
