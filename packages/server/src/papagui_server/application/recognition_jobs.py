"""Durable, deduplicated customer rechecks sharing the index writer lock."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import threading
import uuid

from papagui_server.application.ports import RunStateRepositoryPort
from papagui_server.domain.errors import ResourceBusyError, ResourceNotFoundError
from papagui_server.domain.models import utc_now


class CustomerRecognitionJobs:
    def __init__(self, *, store: RunStateRepositoryPort, operation_lock,
                 validate: Callable[[int], None], execute: Callable,
                 queue_limit: int = 100):
        self._store = store
        self._operation_lock = operation_lock
        self._validate = validate
        self._execute = execute
        self._limit = queue_limit
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._cancelled: set[str] = set()
        self._thread: threading.Thread | None = None
        payload = store.load() or {}
        self._jobs = [j for j in payload.get("jobs", []) if self._valid_job(j)][-queue_limit:]
        known_ids = {job["id"] for job in self._jobs}
        self._cancelled = {
            identifier for identifier in payload.get("cancelled_job_ids", [])
            if isinstance(identifier, str) and identifier in known_ids
        }
        for job in self._jobs:
            if job["state"] in {"queued", "running"} and job["id"] in self._cancelled:
                job.update(state="cancelled", error="", finished_at=utc_now())
            elif job["state"] == "running":
                job.update(state="queued", error="", finished_at="")
        self._persist()

    @staticmethod
    def _valid_job(job):
        return (isinstance(job, dict) and isinstance(job.get("id"), str)
                and ((isinstance(job.get("customer_id"), int) and not isinstance(job["customer_id"], bool)
                      and job["customer_id"] > 0 and job.get("mode") in {"reassess", "extract"})
                     or (job.get("customer_id") is None and job.get("mode") == "rebuild"))
                and job.get("state") in {"queued", "running", "completed", "error", "cancelled"}
                and all(isinstance(job.get(k), str) for k in ("created_at", "finished_at", "error")))

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopped.clear()
            self._thread = threading.Thread(target=self._loop, name="papagui-customer-rechecks", daemon=True)
            self._thread.start()
            self._wake.set()

    def stop(self, *, timeout: float = 15):
        self._stopped.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        if self._thread is None or not self._thread.is_alive():
            self._thread = None

    def request(self, customer_id, *, mode="reassess"):
        if not ((customer_id is None and mode == "rebuild") or (
            isinstance(customer_id, int) and not isinstance(customer_id, bool)
            and customer_id > 0 and mode in {"reassess", "extract"}
        )):
            raise ValueError("Unbekannter Prüfmodus.")
        self._validate(customer_id)
        with self._lock:
            for job in self._jobs:
                if job["customer_id"] == customer_id and job["mode"] == mode and job["state"] in {"queued", "running"}:
                    return deepcopy(job)
            if sum(j["state"] in {"queued", "running"} for j in self._jobs) >= self._limit:
                raise ResourceBusyError("Die Warteschlange ist voll. Bitte später erneut versuchen.")
            job = dict(id=uuid.uuid4().hex, customer_id=customer_id, mode=mode,
                       state="queued", created_at=utc_now(), finished_at="", error="")
            self._jobs.append(job)
            completed = [j for j in self._jobs if j["state"] not in {"queued", "running"}]
            for expired in completed[:max(0, len(self._jobs)-self._limit)]:
                self._jobs.remove(expired)
                self._cancelled.discard(expired["id"])
            self._persist()
        self._wake.set()
        return deepcopy(job)

    def latest(self, customer_id):
        with self._lock:
            return next((deepcopy(j) for j in reversed(self._jobs) if j["customer_id"] == customer_id), None)

    def cancel(self, customer_id, job_id):
        with self._lock:
            job = next((j for j in self._jobs if j["id"] == job_id and j["customer_id"] == customer_id), None)
            if job is None:
                raise ResourceNotFoundError("Prüfauftrag nicht gefunden.")
            if job["state"] in {"queued", "running"}:
                self._cancelled.add(job_id)
                if job["state"] == "queued":
                    job.update(state="cancelled", finished_at=utc_now())
                self._persist()
            return deepcopy(job)

    def run_pending(self):
        """Drain queued work; synchronous entry point for workers and tests."""
        while not self._stopped.is_set():
            with self._lock:
                job = next((j for j in self._jobs if j["state"] == "queued"), None)
            if job is None:
                return
            # Timed lock acquisition keeps shutdown responsive during long indexing.
            if not self._operation_lock.acquire(timeout=0.2):
                continue
            try:
                if self._stopped.is_set():
                    return
                with self._lock:
                    if job["state"] != "queued":
                        continue
                    job.update(state="running", error="")
                    self._persist()
                def cancelled():
                    return self._stopped.is_set() or job["id"] in self._cancelled
                try:
                    if cancelled():
                        raise InterruptedError
                    self._execute(job["customer_id"], job["mode"], cancelled)
                    state, error = ("cancelled", "") if cancelled() else ("completed", "")
                except InterruptedError:
                    state, error = "cancelled", ""
                except Exception:
                    # Parser errors may contain source text or paths; store a code only.
                    state, error = "error", "recognition_failed"
                with self._lock:
                    if self._stopped.is_set() and job["id"] not in self._cancelled:
                        job.update(state="queued", finished_at="", error="")
                    else:
                        job.update(state=state, finished_at=utc_now(), error=error)
                    self._persist()
            finally:
                self._operation_lock.release()

    def _loop(self):
        while not self._stopped.is_set():
            self._wake.wait()
            self._wake.clear()
            self.run_pending()

    def _persist(self):
        self._store.save({"version": 1, "jobs": deepcopy(self._jobs),
                          "cancelled_job_ids": sorted(self._cancelled)})
