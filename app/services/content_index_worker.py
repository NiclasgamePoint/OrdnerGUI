from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from app.core.content_index import (
    ContentProgress,
    ContentStateRepository,
    ShardRepository,
)
from app.core.index_layout import IndexLayout


class DocumentTextExtractor(Protocol):
    """Boundary for document formats; workers do not depend on catalog storage."""

    def extract(self, path: Path) -> tuple[str, str, str]:
        """Return text, status and a diagnostic error."""


@dataclass(frozen=True)
class ContentWorkerResult:
    processed: int
    failed: int
    cancelled: bool
    progress: ContentProgress


class ContentIndexWorker:
    """Sequential, resumable content pipeline suitable for slow storage."""

    def __init__(
        self,
        layout: IndexLayout,
        extractor: DocumentTextExtractor,
        *,
        shard_target_bytes: int,
        maximum_attempts: int = 3,
    ):
        self.layout = layout
        self.extractor = extractor
        self.shard_target_bytes = shard_target_bytes
        self.maximum_attempts = maximum_attempts

    def run(
        self,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: Callable[[ContentProgress, str], None] | None = None,
        maximum_priority: int | None = None,
    ) -> ContentWorkerResult:
        processed = 0
        failed = 0
        cancelled = False
        with ContentStateRepository.open_recoverable(self.layout) as state:
            state.recover_expired_leases()
            shards = ShardRepository(
                self.layout, state, target_bytes=self.shard_target_bytes
            )
            while True:
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    break
                task = state.acquire_next(
                    self.maximum_attempts, maximum_priority=maximum_priority
                )
                if task is None:
                    break
                if progress_callback is not None:
                    progress_callback(state.progress(), task.path)
                try:
                    text, status, error = self.extractor.extract(Path(task.path))
                    if error and status in {"error", "timeout"}:
                        raise RuntimeError(error)
                    shard_name = shards.store(task, text)
                    state.complete(
                        task,
                        shard_name=shard_name,
                        content_status=status,
                        extracted_characters=len(text),
                        content_error=error,
                    )
                    processed += 1
                except Exception as exc:
                    state.fail(task, str(exc), self.maximum_attempts)
                    if task.attempts >= self.maximum_attempts:
                        failed += 1
                if progress_callback is not None:
                    progress_callback(state.progress(), task.path)
            progress = state.progress()
        return ContentWorkerResult(processed, failed, cancelled, progress)
