from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.core.config import load_index_options
from app.core.config import load_customer_recognition_options
from app.core.catalog_index import CatalogIndexManager
from app.core.content_index import (
    DEFAULT_SHARD_TARGET_BYTES,
    ContentStateRepository,
    ShardRepository,
    ShardMaintenanceService,
)
from app.core.index_job_state import cancel_path, utc_now, write_state
from app.core.index_layout import IndexLayout
from app.core.logging_config import configure_logging
from app.core.process_support import suppress_windows_crash_dialogs
from app.services.content_index_worker import ContentIndexWorker, ContentWorkerResult
from app.services.document_text_indexer import DocumentTextIndexer
from app.services.customer_recognition import CustomerRecognitionService


class ContentIndexJobRunner:
    """Detached content job that can be resumed from its durable queue."""

    def __init__(
        self,
        layout: IndexLayout,
        state_dir: Path,
        customer_database_path: Path,
        shard_target_bytes: int = DEFAULT_SHARD_TARGET_BYTES,
    ):
        self.layout = layout
        self.state_dir = state_dir
        self.customer_database_path = customer_database_path
        self.shard_target_bytes = shard_target_bytes

    def run(self) -> int:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        write_state(self.state_dir, {
            "status": "running", "started_at": utc_now(), "pid": os.getpid()
        })
        try:
            self._reconcile_queue()
            worker = ContentIndexWorker(
                self.layout,
                DocumentTextIndexer(load_index_options()),
                shard_target_bytes=self.shard_target_bytes,
            )
            priority_result = worker.run(
                should_cancel=lambda: cancel_path(self.state_dir).exists(),
                progress_callback=self._progress,
                maximum_priority=10,
            )
            if priority_result.cancelled:
                result = priority_result
            else:
                self._enrich_customers()
                result = worker.run(
                    should_cancel=lambda: cancel_path(self.state_dir).exists(),
                    progress_callback=self._progress,
                )
                result = ContentWorkerResult(
                    processed=priority_result.processed + result.processed,
                    failed=priority_result.failed + result.failed,
                    cancelled=result.cancelled,
                    progress=result.progress,
                )
            if not result.cancelled:
                with ContentStateRepository.open_recoverable(self.layout) as state:
                    maintenance = ShardMaintenanceService(
                        self.layout, state
                    ).maintain()
                if maintenance["requeued"]:
                    recovery = worker.run(
                        should_cancel=lambda: cancel_path(self.state_dir).exists(),
                        progress_callback=self._progress,
                    )
                    result = ContentWorkerResult(
                        processed=result.processed + recovery.processed,
                        failed=result.failed + recovery.failed,
                        cancelled=recovery.cancelled,
                        progress=recovery.progress,
                    )
            write_state(self.state_dir, {
                "status": "cancelled" if result.cancelled else "completed",
                "processed_count": result.processed,
                "failed_count": result.failed,
                **self._progress_values(result.progress),
            })
            return 2 if result.cancelled else 0
        except Exception as exc:
            write_state(self.state_dir, {"status": "error", "error": str(exc)})
            return 1

    def _reconcile_queue(self):
        """Rebuild the durable queue from the catalog after loss or corruption."""
        manager = CatalogIndexManager(self.layout.catalog_path, initialize=False)
        try:
            with ContentStateRepository.open_recoverable(self.layout) as state:
                manager.reconcile_content_state(
                    state,
                    ShardRepository(
                        self.layout,
                        state,
                        target_bytes=self.shard_target_bytes,
                    ),
                    load_customer_recognition_options().preferred_patterns,
                )
        finally:
            manager.close()

    def _enrich_customers(self):
        CustomerRecognitionService(
            self.layout.catalog_path,
            self.customer_database_path,
            load_customer_recognition_options(),
            content_layout=self.layout,
        ).synchronize(include_documents=True)

    @staticmethod
    def _progress_values(progress) -> dict:
        return {
            "total_documents": progress.total_documents,
            "completed_documents": progress.completed_documents,
            "pending_documents": progress.pending_documents,
            "failed_documents": progress.failed_documents,
            "total_bytes": progress.total_bytes,
            "completed_bytes": progress.completed_bytes,
            "complete": progress.complete,
        }

    def _progress(self, progress, current_path: str):
        write_state(self.state_dir, {
            "status": "running",
            "current_path": current_path,
            **self._progress_values(progress),
        })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI content index job")
    parser.add_argument("--index-root", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--customers", required=True, type=Path)
    parser.add_argument(
        "--shard-target-bytes", type=int, default=DEFAULT_SHARD_TARGET_BYTES
    )
    return parser


def main() -> int:
    suppress_windows_crash_dialogs()
    configure_logging()
    arguments = build_parser().parse_args()
    return ContentIndexJobRunner(
        IndexLayout(arguments.index_root.resolve()),
        arguments.state_dir.resolve(),
        arguments.customers.resolve(),
        arguments.shard_target_bytes,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
