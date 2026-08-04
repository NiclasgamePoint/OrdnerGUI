from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import time
from typing import Callable, Protocol

from app.core.content_index import ContentProgress, ContentStateRepository, ContentTask, ShardRepository
from app.core.index_layout import IndexLayout
from app.services.extraction_models import ExtractionResult
from app.services.index_resource_policy import IndexResourcePolicy


LOGGER = logging.getLogger(__name__)


class DocumentTextExtractor(Protocol):
    def extract(self, path: Path) -> ExtractionResult: ...


@dataclass(frozen=True)
class ContentWorkerResult:
    processed: int
    failed: int
    cancelled: bool
    progress: ContentProgress


class ContentIndexWorker:
    """Parallel extraction pipeline with one deterministic SQLite writer."""

    def __init__(
        self,
        layout: IndexLayout,
        extractor: DocumentTextExtractor,
        *,
        shard_target_bytes: int,
        maximum_attempts: int = 3,
        pause_seconds: float = 0.0,
        newest_years_first: bool = True,
        maximum_workers: int = 1,
        resource_policy: IndexResourcePolicy | None = None,
        resource_profile: str = "balanced",
    ):
        self.layout = layout
        self.extractor = extractor
        self.shard_target_bytes = shard_target_bytes
        self.maximum_attempts = maximum_attempts
        self.pause_seconds = max(0.0, float(pause_seconds))
        self.newest_years_first = bool(newest_years_first)
        self.maximum_workers = max(1, min(20, int(maximum_workers)))
        self.resource_policy = resource_policy
        self.resource_profile = resource_profile

    def run(
        self,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: Callable[[ContentProgress, str], None] | None = None,
        activity_callback: Callable[[list[dict[str, object]]], None] | None = None,
        maximum_priority: int | None = None,
    ) -> ContentWorkerResult:
        processed = failed = 0
        cancelled = False
        parser_counts: dict[str, int] = {}
        timeout_count = 0
        slowest: list[tuple[float, str]] = []
        run_started = time.monotonic()
        with ContentStateRepository.open_recoverable(self.layout) as state:
            state.recover_expired_leases()
            shards = ShardRepository(self.layout, state, target_bytes=self.shard_target_bytes)
            futures: dict[Future, tuple[ContentTask, int]] = {}
            with ThreadPoolExecutor(max_workers=self.maximum_workers, thread_name_prefix="content") as executor:
                exhausted = False
                while futures or not exhausted:
                    if should_cancel is not None and should_cancel():
                        cancelled = True
                        exhausted = True
                    allowed = self._current_limit()
                    while not exhausted and len(futures) < allowed:
                        task = state.acquire_next(
                            self.maximum_attempts,
                            maximum_priority=maximum_priority,
                            newest_years_first=self.newest_years_first,
                        )
                        if task is None:
                            exhausted = True
                            break
                        if self.pause_seconds:
                            time.sleep(self.pause_seconds)
                        used_slots = {slot for _task, slot in futures.values()}
                        worker_slot = next(
                            slot for slot in range(1, self.maximum_workers + 1)
                            if slot not in used_slots
                        )
                        futures[executor.submit(
                            self.extractor.extract, Path(task.path)
                        )] = (task, worker_slot)
                        if progress_callback is not None:
                            progress_callback(state.progress(), task.path)
                    self._report_activity(futures, activity_callback)
                    if not futures:
                        break
                    done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                    for future in done:
                        task, _worker_slot = futures.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = ExtractionResult(status="error", error=str(exc))
                        if not isinstance(result, ExtractionResult):
                            text, status, error = result
                            result = ExtractionResult(
                                text=text, status=status, error=error,
                                file_type=Path(task.path).suffix.lower().lstrip("."),
                                source_size=Path(task.path).stat().st_size,
                            )
                        self._log_result(task, result)
                        parser_counts[result.parser or "unknown"] = (
                            parser_counts.get(result.parser or "unknown", 0) + 1
                        )
                        timeout_count += int(result.status == "timeout")
                        slowest.append((result.duration_seconds, task.path))
                        slowest = sorted(slowest, reverse=True)[:10]
                        if result.status in {"error", "timeout"}:
                            state.fail(
                                task,
                                result.error,
                                self.maximum_attempts,
                                category=result.status,
                                retry_later_only=result.status == "timeout",
                                last_parser=result.parser,
                            )
                            if result.status == "timeout" or task.attempts >= self.maximum_attempts:
                                failed += 1
                        else:
                            shard_name = shards.store(task, result.text)
                            state.complete(
                                task,
                                shard_name=shard_name,
                                content_status=result.status,
                                extracted_characters=len(result.text),
                                content_error=result.error,
                                last_parser=result.parser,
                            )
                            processed += 1
                        if progress_callback is not None:
                            progress_callback(state.progress(), task.path)
                    self._report_activity(futures, activity_callback)
            progress = state.progress()
        elapsed = max(0.0001, time.monotonic() - run_started)
        LOGGER.info(json.dumps({
            "event": "content_index_summary",
            "processed": processed,
            "failed": failed,
            "timeouts": timeout_count,
            "documents_per_second": round((processed + failed) / elapsed, 3),
            "parsers": parser_counts,
            "slowest": [
                {"duration_s": round(duration, 4), "path": path}
                for duration, path in slowest
            ],
        }, ensure_ascii=False, separators=(",", ":")))
        return ContentWorkerResult(processed, failed, cancelled, progress)

    @staticmethod
    def _report_activity(
        futures: dict[Future, tuple[ContentTask, int]],
        callback: Callable[[list[dict[str, object]]], None] | None,
    ):
        if callback is None:
            return
        callback([
            {"worker": slot, "path": task.path}
            for task, slot in sorted(futures.values(), key=lambda item: item[1])
        ])

    def _current_limit(self) -> int:
        if self.resource_policy is None:
            return self.maximum_workers
        return min(
            self.maximum_workers,
            self.resource_policy.worker_limit(self.resource_profile),
        )

    @staticmethod
    def _log_result(task: ContentTask, result: ExtractionResult):
        payload = {
            "event": "content_extraction",
            "path": task.path,
            "type": result.file_type,
            "bytes": result.source_size,
            "pages": result.page_count,
            "parser": result.parser,
            "status": result.status,
            "characters": len(result.text),
            "duration_s": round(result.duration_seconds, 4),
            "parser_s": round(result.parser_seconds, 4),
            "ocr_s": round(result.ocr_seconds, 4),
            "ocr_pages": result.ocr_pages,
            "attempt": task.attempts,
        }
        log = LOGGER.warning if result.status in {"error", "timeout"} else LOGGER.info
        log(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
