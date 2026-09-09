"""Bounded parallel readers with one catalog/cache writer and resumable checkpoints."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
import hashlib
import threading
import time

from papagui_server.adapters.catalog_extraction import (
    DocumentTextExtractor,
    ExtractionResourcePolicy,
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
)
from papagui_server.adapters.extraction_store import artifact_identity
from papagui_server.domain.document_extraction import ExtractionBlock, ExtractionResult


class DocumentWorkStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self._started = 0.0
        self._finished = 0.0
        self._values = self._empty()

    @staticmethod
    def _empty():
        return dict(
            state="idle",
            worker_limit=0,
            active_workers=0,
            queued_documents=0,
            discovered_documents=0,
            processed_documents=0,
            reused_documents=0,
            extracted_documents=0,
            failed_documents=0,
            read_documents=0,
            verify_content=False,
        )

    def begin(self, workers, verify):
        with self._lock:
            self._values = {
                **self._empty(),
                "state": "running",
                "worker_limit": workers,
                "verify_content": bool(verify),
            }
            self._started, self._finished = time.monotonic(), 0.0

    def change(self, **values):
        with self._lock:
            self._values.update(values)

    def count(self, **increments):
        with self._lock:
            for key, value in increments.items():
                self._values[key] += value

    def finish(self, state):
        with self._lock:
            self._values.update(state=state, active_workers=0, queued_documents=0)
            self._finished = time.monotonic()

    def snapshot(self):
        with self._lock:
            elapsed = (self._finished or time.monotonic()) - self._started if self._started else 0
            return {**self._values, "elapsed_seconds": round(elapsed, 3)}


@dataclass(frozen=True)
class ReadResult:
    extraction: ExtractionResult
    needs_store: bool = False
    reused: bool = False


class SourceChangedError(Exception):
    """A reader must never publish text under an earlier source fingerprint."""


class ParallelDocuments:
    def __init__(self, *, extractor, store, writer, settings, workers, status, cancelled):
        self.extractor, self.store, self.writer = extractor, store, writer
        self.settings, self.workers, self.status = settings, workers, status
        self._external_cancelled = cancelled
        self._stop = threading.Event()
        self._cache_lock = threading.Lock()
        self._recent: OrderedDict[tuple, Future] = OrderedDict()

    def cancelled(self):
        return self._stop.is_set() or self._external_cancelled()

    def check_cancelled(self):
        if self.cancelled():
            self.status.change(state="cancelling")
            raise InterruptedError("Indexlauf abgebrochen.")

    def _load(self, path, stat, version, forced):
        self.check_cancelled()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            self.status.count(read_documents=1)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                self.check_cancelled()
                digest.update(chunk)
        content_hash = digest.hexdigest()
        # Share in-flight copies, without allowing the result buffer to grow
        # with the corpus. Explicit re-extraction still reads every document.
        key = (content_hash, version, str(path) if forced else "")
        with self._cache_lock:
            future = self._recent.get(key)
            leader = future is None
            if leader:
                future = Future()
                self._recent[key] = future
            self._recent.move_to_end(key)
            for old_key, old in list(self._recent.items()):
                if len(self._recent) <= self.workers * 2:
                    break
                if old.done():
                    del self._recent[old_key]
        if not leader:
            while not future.done():
                self.check_cancelled()
                wait((future,), timeout=0.05)
            try:
                result = future.result()
            except SourceChangedError:
                # The leader changed, but this copy may still be readable.
                return self._load(path, stat, version, forced)
            return replace(result, reused=True)
        try:
            cached = (
                None
                if forced
                else self.store.peek(
                    content_hash,
                    version,
                    max_attempts=self.settings.extraction_retry_attempts,
                )
            )
            if cached is not None:
                result = ReadResult(cached, reused=True)
            else:
                self.status.count(extracted_documents=1)
                try:
                    if hasattr(self.extractor, "extract_document"):
                        extraction = self.extractor.extract_document(
                            path, self.settings, self.cancelled
                        )
                    else:
                        text = self.extractor.extract(path, self.settings, self.cancelled)
                        extraction = ExtractionResult(
                            text=text,
                            blocks=(ExtractionBlock(text),) if text else (),
                            status="ok" if text else "no_text",
                        )
                except InterruptedError:
                    raise
                except Exception:
                    extraction = ExtractionResult(status="error", reason="document_read_failed")
                self.check_cancelled()
                extraction = replace(extraction, content_hash=content_hash, version_hash=version)
                extraction = replace(extraction, artifact_hash=artifact_identity(extraction))
                result = ReadResult(extraction, needs_store=True)
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                raise SourceChangedError()
            future.set_result(result)
            return result
        except BaseException as error:
            with self._cache_lock:
                if self._recent.get(key) is future:
                    del self._recent[key]
            future.set_exception(error)
            raise

    def _read(self, path, stat, version, forced):
        self.status.count(queued_documents=-1, active_workers=1)
        try:
            result = self._load(path, stat, version, forced)
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                return ReadResult(
                    ExtractionResult(
                        status="error",
                        reason="source_changed_during_extraction",
                        version_hash=version,
                    )
                )
            return result
        except SourceChangedError:
            return ReadResult(
                ExtractionResult(
                    status="error", reason="source_changed_during_extraction", version_hash=version
                )
            )
        except InterruptedError:
            raise
        except Exception:
            # Never put paths, document contents or parser exception text in status.
            return ReadResult(
                ExtractionResult(
                    status="error", reason="document_read_failed", version_hash=version
                )
            )
        finally:
            self.status.count(active_workers=-1)

    def run(
        self,
        *,
        connection,
        files,
        source_path,
        source_id,
        parser,
        known,
        root_ids,
        parser_fingerprint,
        verify_content,
        forced,
        selected_projects,
        run_id,
        progress,
    ):
        seen, pending, formats, stored = set(), {}, {}, {}
        processed = 0
        executor = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="papagui-document"
        )

        def complete(job, result=None):
            nonlocal processed
            path, relative, stat, metadata, eligible, version = job
            if result is None:
                self.writer.update_file_classification(connection, source_id, relative, metadata)
                self.status.count(reused_documents=1)
            else:
                extraction = result.extraction
                identity = (extraction.content_hash, extraction.artifact_hash)
                if result.needs_store:
                    if identity not in stored:
                        stored[identity] = self.store.put(
                            extraction,
                            retry_delay_seconds=self.settings.extraction_retry_delay_seconds,
                            max_store_mb=self.settings.extraction_store_max_mb,
                        )
                    if not stored[identity]:
                        extraction = replace(
                            extraction, status="partial", reason="artifact_store_budget"
                        )
                self.writer.upsert_file(
                    connection,
                    source_id=source_id,
                    relative_path=relative,
                    path=path,
                    stat=stat,
                    content=extraction.text,
                    eligible=eligible,
                    metadata=metadata,
                    extraction=extraction,
                )
                self.status.count(
                    reused_documents=int(result.reused),
                    failed_documents=int(
                        extraction.status
                        in {"error", "timeout", "tool_missing", "partial", "too_large"}
                    ),
                )
            connection.execute(
                "UPDATE files SET extraction_policy_version=?,last_scan_run=? WHERE source_id=? AND relative_path=?",
                (version, run_id, source_id, relative),
            )
            processed += 1
            self.status.count(processed_documents=1)
            if processed % 25 == 0:
                connection.commit()
            progress(processed, relative)

        def drain(block=False):
            while pending:
                self.check_cancelled()
                done, _ = wait(pending, timeout=0.1 if block else 0, return_when=FIRST_COMPLETED)
                if done or not block:
                    break
            else:
                return
            # Submission order gives stable review IDs when several readers finish
            # together, without waiting for a slow earlier document.
            for future in tuple(pending):
                if future in done:
                    complete(pending.pop(future), future.result())

        try:
            for path in files:
                self.check_cancelled()
                drain()
                while len(pending) >= self.workers * 2:
                    drain(True)
                relative = path.relative_to(source_path).as_posix()
                seen.add(relative)
                self.status.count(discovered_documents=1)
                stat = path.stat()
                metadata = parser.parse_file(source_id, relative)
                extension = path.suffix.casefold().lstrip(".")
                eligible = (
                    self.settings.content_indexing_enabled
                    and extension in self.settings.indexed_content_types
                    and extension in SUPPORTED_EXTENSIONS
                    and stat.st_size <= self.settings.max_file_size_mb * 1024 * 1024
                )
                policy = ExtractionResourcePolicy.for_document(path, self.settings)
                ocr_budget = (
                    policy.ocr_pages if extension == "pdf" or extension in IMAGE_EXTENSIONS else 0
                )
                image_preferred = extension in IMAGE_EXTENSIONS and any(
                    word in path.stem.casefold()
                    for word in (
                        "scan",
                        "kontakt",
                        "anschreiben",
                        "auftrag",
                        "angebot",
                        "vertrag",
                        "dokument",
                    )
                )
                if extension not in formats:
                    formats[extension] = (
                        self.extractor.fingerprint(self.settings, extension)
                        if isinstance(self.extractor, DocumentTextExtractor)
                        else parser_fingerprint
                    )
                version = hashlib.sha256(
                    f"{formats[extension]}\0{extension}\0{ocr_budget}\0{image_preferred}".encode()
                ).hexdigest()
                root_path = (
                    metadata.project_root.source.relative_path if metadata.project_root else ""
                )
                force_document = forced and (
                    selected_projects is None or root_ids.get(root_path) in selected_projects
                )
                job = (path, relative, stat, metadata, eligible, version)
                prior = known.get(relative)
                unchanged = prior and (
                    prior["modified_ns"],
                    prior["file_size"],
                    prior["content_eligible"],
                    prior["extraction_policy_version"],
                ) == (stat.st_mtime_ns, stat.st_size, int(eligible), version)
                verified = not verify_content or (prior and prior["last_scan_run"] == run_id)
                completed_in_run = prior and prior["last_scan_run"] == run_id
                if unchanged and verified and (not force_document or completed_in_run):
                    # Failed/partial documents retry when due, while successful
                    # unchanged documents do not open either source bytes or cache.
                    retry = (
                        prior["extraction_reason"] == "artifact_store_budget"
                        or prior["extraction_status"]
                        in {
                            "error",
                            "timeout",
                            "tool_missing",
                            "partial",
                        }
                        and (
                            not prior["content_hash"]
                            or self.store.peek(
                                prior["content_hash"],
                                version,
                                max_attempts=self.settings.extraction_retry_attempts,
                            )
                            is None
                        )
                    )
                    if not retry:
                        complete(job)
                        continue
                if not eligible:
                    if not self.settings.content_indexing_enabled:
                        extraction = ExtractionResult(
                            status="unsupported", reason="content_indexing_disabled"
                        )
                    elif stat.st_size > self.settings.max_file_size_mb * 1024 * 1024:
                        extraction = ExtractionResult(status="too_large", reason="file_size_budget")
                    else:
                        extraction = ExtractionResult(
                            status="unsupported",
                            reason=(
                                "extension_disabled"
                                if extension in SUPPORTED_EXTENSIONS
                                else "unsupported_extension"
                            ),
                        )
                    complete(job, ReadResult(extraction))
                    continue
                self.status.count(queued_documents=1)
                pending[executor.submit(self._read, path, stat, version, force_document)] = job
            while pending:
                drain(True)
            self.check_cancelled()
            return seen
        finally:
            self._stop.set()
            executor.shutdown(wait=True, cancel_futures=True)
            self._recent.clear()
