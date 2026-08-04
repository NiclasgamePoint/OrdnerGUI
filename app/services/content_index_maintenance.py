from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.content_index import ContentStateRepository, ShardMaintenanceService
from app.core.index_layout import IndexLayout
from app.core.index_job_state import cancel_path, state_path


@dataclass(frozen=True)
class ContentMaintenanceResult:
    affected_documents: int = 0
    optimized_shards: int = 0
    compacted_shards: int = 0
    requeued_documents: int = 0


class ContentIndexMaintenance:
    """Perform explicit maintenance on the fully reconstructable content index."""

    def __init__(self, layout: IndexLayout):
        self.layout = layout

    def retry_failed(self) -> ContentMaintenanceResult:
        with ContentStateRepository.open_recoverable(self.layout) as state:
            cursor = state.connection.execute(
                "UPDATE documents SET status='pending',attempts=0,content_status='',"
                "content_error='',error_category='',lease_until='' WHERE status='failed'"
            )
            state.connection.commit()
            return ContentMaintenanceResult(affected_documents=max(cursor.rowcount, 0))

    def rebuild(self) -> ContentMaintenanceResult:
        with ContentStateRepository.open_recoverable(self.layout) as state:
            count = int(state.connection.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0])
            state.connection.execute(
                "UPDATE documents SET status='pending',attempts=0,lease_until='',"
                "shard_name='',content_status='',content_error='',extracted_characters=0"
            )
            state.connection.execute("DELETE FROM shards")
            state.connection.commit()
        self._remove_shard_files()
        return ContentMaintenanceResult(affected_documents=count)

    def optimize(self) -> ContentMaintenanceResult:
        with ContentStateRepository.open_recoverable(self.layout) as state:
            result = ShardMaintenanceService(self.layout, state).maintain()
        return ContentMaintenanceResult(
            optimized_shards=result["optimized"],
            compacted_shards=result["compacted"],
            requeued_documents=result["requeued"],
        )

    def clear(self) -> ContentMaintenanceResult:
        count = 0
        if self.layout.content_state_path.exists():
            with ContentStateRepository.open_recoverable(self.layout) as state:
                count = int(state.connection.execute(
                    "SELECT COUNT(*) FROM documents"
                ).fetchone()[0])
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.layout.content_state_path}{suffix}").unlink(missing_ok=True)
        self._remove_shard_files()
        if self.layout.corrupt_shard_dir.exists():
            for path in self.layout.corrupt_shard_dir.iterdir():
                if path.is_file():
                    path.unlink(missing_ok=True)
        content_job_dir = self.layout.jobs_dir / "content"
        state_path(content_job_dir).unlink(missing_ok=True)
        cancel_path(content_job_dir).unlink(missing_ok=True)
        return ContentMaintenanceResult(affected_documents=count)

    def _remove_shard_files(self):
        if not self.layout.shard_dir.exists():
            return
        for path in self.layout.shard_dir.iterdir():
            if path.is_file() and (
                path.suffix == ".db" or path.name.endswith((".db-wal", ".db-shm"))
            ):
                path.unlink(missing_ok=True)
