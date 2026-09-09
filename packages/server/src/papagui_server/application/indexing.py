"""Single-writer scheduler and coordinator for complete index runs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
import shutil
import threading
import time
from typing import Any
import uuid

from papagui_contracts import (
    IndexProgress,
    IndexRunState,
    IndexStatus,
    ServerState,
    ServerStatus,
    SourcePath,
)

from papagui_server import __version__
from papagui_server.application.ports import (
    CatalogIndexerPort,
    CustomerRecognizerPort,
    GenerationPublisherPort,
    RunStateRepositoryPort,
    SourceIdentityGuardPort,
    SettingsRepositoryPort,
)
from papagui_server.domain.errors import ResourceBusyError
from papagui_server.domain.models import MutableRunState, ServerSettings, utc_now


logger = logging.getLogger(__name__)


class IndexRunCoordinator:
    """Own scheduling, cancellation, state, and the exclusive writer rule."""

    def __init__(
        self,
        *,
        source_path: Path,
        source_id: str,
        data_path: Path,
        settings: SettingsRepositoryPort,
        catalog: CatalogIndexerPort,
        recognizer: CustomerRecognizerPort,
        publisher: GenerationPublisherPort,
        run_state: RunStateRepositoryPort | None = None,
        source_guard: SourceIdentityGuardPort | None = None,
    ) -> None:
        self.source_path = source_path.resolve()
        self.source_id = source_id
        self.data_path = data_path.resolve()
        self._settings = settings
        self._catalog = catalog
        self._recognizer = recognizer
        self._publisher = publisher
        self._run_state = run_state
        self._source_guard = source_guard
        self._state = MutableRunState()
        self._started_at = utc_now()
        self._started_monotonic = time.monotonic()
        self._lock = threading.RLock()
        self.operation_lock = threading.RLock()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._stopped = threading.Event()
        self._restart_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._queued_run = False
        self._queued_run_id = ""
        self._queued_full_rebuild = False
        self._queued_delete = False
        self._schedule_reset = False
        self._next_due = time.monotonic()
        self._recover_persisted_state()

    def start(self, *, run_on_start: bool = True) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._queued_run = run_on_start
            self._queued_run_id = uuid.uuid4().hex if run_on_start else ""
            self._queued_full_rebuild = run_on_start and not (
                self.data_path / "index" / "catalog" / "active.db"
            ).exists()
            self._next_due = (
                time.monotonic()
                if run_on_start
                else time.monotonic() + self._settings.load().interval_seconds
            )
            self._thread = threading.Thread(
                target=self._scheduler_loop,
                kwargs={"defer_overdue": not run_on_start},
                name="papagui-index-scheduler",
                daemon=True,
            )
            self._thread.start()
            if run_on_start:
                self._wake.set()

    def stop(self, *, timeout: float = 15.0) -> None:
        self._stopped.set()
        self._cancel.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested.is_set()

    def wait_for_restart(self, timeout: float) -> bool:
        return self._restart_requested.wait(timeout)

    def settings_changed(self, _settings: ServerSettings) -> None:
        with self._lock:
            self._schedule_reset = True
        self._wake.set()

    def request_run(self, *, full_rebuild: bool = False) -> dict[str, Any]:
        with self._lock:
            self._queued_run = True
            if not self._queued_run_id:
                self._queued_run_id = uuid.uuid4().hex
            self._queued_full_rebuild = self._queued_full_rebuild or full_rebuild
            self._state.queued_action = "rebuild" if full_rebuild else "run"
            queued = self._state.state in {"running", "cancelling"}
            requested_id = self._queued_run_id
            self._persist_state()
        self._wake.set()
        return {"accepted": True, "run_id": requested_id, "queued": queued}

    def request_cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._state.state != "running":
                raise ResourceBusyError("Es läuft derzeit kein Indexjob.")
            self._state.state = "cancelling"
            self._state.queued_action = "cancel"
            self._persist_state()
        self._cancel.set()
        return {"accepted": True, "run_id": self._state.run_id}

    def request_delete(self, *, rebuild: bool = True) -> dict[str, Any]:
        with self._lock:
            self._queued_delete = True
            self._queued_run = rebuild
            self._queued_full_rebuild = rebuild
            self._state.queued_action = "delete-and-rebuild" if rebuild else "delete"
            if self._state.state in {"running", "cancelling"}:
                self._cancel.set()
            self._persist_state()
        self._wake.set()
        return {"accepted": True, "rebuild": rebuild}

    def request_restart(self) -> dict[str, Any]:
        with self._lock:
            self._state.queued_action = "restart"
            self._persist_state()
        self._restart_requested.set()
        self._cancel.set()
        self._wake.set()
        return {"accepted": True, "restart": True}

    def run_once(self, *, full_rebuild: bool = False) -> int:
        """Run synchronously for CLI `--once` and deterministic tests."""
        with self._lock:
            if self._state.state in {"running", "cancelling"}:
                raise ResourceBusyError("Es läuft bereits ein Indexjob.")
        return self._execute_run(full_rebuild)

    def status(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._state.snapshot().to_dict()
        current = self._publisher.current()
        components = dict((current or {}).get("components") or {})
        current_path = str(snapshot["current_path"])
        state_value = str(snapshot["state"])
        state_aliases = {"cancelling": "running", "deleted": "idle", "stopped": "idle"}
        try:
            current_source = SourcePath(self.source_id, current_path) if current_path else None
            legacy_current_path = None
        except ValueError:
            current_source = None
            legacy_current_path = current_path or None
        index_status = IndexStatus(
            state=IndexRunState.parse(state_aliases.get(state_value, state_value)),
            progress=IndexProgress(
                processed_items=int(snapshot["processed_count"]),
                phase=str(snapshot["phase"]),
                current_source=current_source,
                legacy_current_path=legacy_current_path,
            ),
            run_id=str(snapshot["run_id"]) or None,
            message=str(snapshot["error"]) or None,
            started_at=str(snapshot["started_at"]) or None,
            updated_at=utc_now(),
            finished_at=str(snapshot["completed_at"]) or None,
        )
        source_available, source_message = self._source_probe()
        server_state = (
            ServerState.STOPPING
            if self._stopped.is_set() or self.restart_requested
            else ServerState.ONLINE
            if source_available
            else ServerState.DEGRADED
        )
        payload = ServerStatus(
            state=server_state,
            index=index_status,
            server_version=__version__,
            uptime_seconds=max(0, int(time.monotonic() - self._started_monotonic)),
            observed_at=utc_now(),
            active_index_generation=(components.get("index") or {}).get("generation"),
            active_customer_generation=(components.get("customers") or {}).get("generation"),
            message=source_message or None,
        ).to_dict()
        payload["source_id"] = self.source_id
        payload["source_available"] = source_available
        payload["settings"] = self._settings.load().to_dict()
        payload["backups"] = self._generation_backup_counts()
        retention = getattr(self._publisher, "retention_status", None)
        payload["retention"] = (
            retention()
            if retention is not None
            else {"state": "ok", "required_predecessors": 3, "failures": {}}
        )
        payload["queued_action"] = str(snapshot["queued_action"])
        payload["resumable"] = (
            self.data_path / "index" / "catalog" / "builds" / "resume.db"
        ).is_file()
        payload["document_workers"] = self.document_worker_status()
        return payload

    def document_worker_status(self) -> dict[str, Any] | None:
        status = getattr(self._catalog, "status", None)
        return status() if status is not None else None

    def _content_verification_due(self) -> bool:
        remaining = self._content_verification_delay(self._settings.load())
        return remaining is not None and remaining <= 0

    def _content_verification_delay(self, settings: ServerSettings) -> float | None:
        if not getattr(settings, "daily_reconciliation_enabled", False):
            return None
        try:
            last_verified = getattr(self._catalog, "last_content_verification_at", None)
            timestamp = last_verified() if last_verified is not None else None
            if not timestamp:
                return 0.0
            verified = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if verified.tzinfo is None:
                verified = verified.replace(tzinfo=timezone.utc)
            return max(0.0, 86_400 - (datetime.now(timezone.utc) - verified).total_seconds())
        except Exception:
            # Missing/unreadable scheduling metadata must not stop the scheduler.
            # A failing build follows the ordinary bounded retry schedule below.
            return 0.0

    def _scheduled_delay(self, settings: ServerSettings, *, defer_overdue: bool = False) -> float:
        remaining = self._content_verification_delay(settings)
        if remaining is None:
            return settings.interval_seconds
        if defer_overdue and remaining <= 0:
            # Failed or cancelled verification does not advance the durable
            # timestamp, but must not cause a zero-delay retry loop. The same
            # bounded delay respects an explicitly disabled startup run.
            remaining = 86_400
        return min(settings.interval_seconds, remaining)

    def _scheduler_loop(self, *, defer_overdue: bool = False) -> None:
        next_due = min(
            self._next_due,
            time.monotonic() + self._scheduled_delay(
                self._settings.load(), defer_overdue=defer_overdue
            ),
        )
        while not self._stopped.is_set() and not self._restart_requested.is_set():
            settings = self._settings.load()
            timeout = max(0.0, next_due - time.monotonic()) if settings.automatic_runs_enabled else None
            self._wake.wait(timeout)
            self._wake.clear()
            if self._stopped.is_set() or self._restart_requested.is_set():
                break
            # A settings-change wake must use the newly saved interval/profile.
            settings = self._settings.load()
            with self._lock:
                delete = self._queued_delete
                requested = self._queued_run
                requested_id = self._queued_run_id
                rebuild = self._queued_full_rebuild
                reset_schedule = self._schedule_reset
                self._queued_delete = False
                self._queued_run = False
                self._queued_run_id = ""
                self._queued_full_rebuild = False
                self._schedule_reset = False
            if reset_schedule:
                next_due = time.monotonic() + self._scheduled_delay(settings)
            due = settings.automatic_runs_enabled and time.monotonic() >= next_due
            if delete:
                self._delete_index()
            if requested or due:
                self._execute_run(
                    rebuild or delete, run_id=requested_id or None
                )
                next_due = time.monotonic() + self._scheduled_delay(
                    self._settings.load(), defer_overdue=True
                )
            elif settings.automatic_runs_enabled:
                next_due = min(next_due, time.monotonic() + settings.interval_seconds)
        with self._lock:
            if self._state.state not in {"error", "completed", "cancelled"}:
                self._state.state = "stopped"
            self._persist_state()

    def _execute_run(self, full_rebuild: bool, *, run_id: str | None = None) -> int:
        while not self._stopped.is_set() and not self._restart_requested.is_set():
            if self.operation_lock.acquire(timeout=0.2):
                break
        else:
            return 1
        try:
            if self._stopped.is_set() or self._restart_requested.is_set():
                return 1
            return self._execute_run_locked(full_rebuild, run_id=run_id)
        finally:
            self.operation_lock.release()

    def _execute_run_locked(self, full_rebuild: bool, *, run_id: str | None = None) -> int:
        self._cancel.clear()
        if self._stopped.is_set() or self._restart_requested.is_set():
            return 1
        run_id = run_id or uuid.uuid4().hex
        with self._lock:
            self._state = MutableRunState(
                run_id=run_id,
                state="running",
                phase="catalog",
                full_rebuild=full_rebuild,
                started_at=utc_now(),
            )
            self._persist_state()
        try:
            if self._source_guard is not None:
                self._source_guard.ensure_ready(self.source_path, self.source_id)
            self._catalog.build(
                self.source_path,
                source_id=self.source_id,
                full_rebuild=full_rebuild,
                verify_content=full_rebuild or self._content_verification_due(),
                settings=self._settings.load(),
                cancelled=self._cancel.is_set,
                progress=self._progress,
            )
            if self._cancel.is_set():
                raise InterruptedError("Indexlauf abgebrochen.")
            self._set_phase("customer-recognition")
            recognition = self._recognizer.synchronize(
                self.source_path,
                source_id=self.source_id,
                minimum_year=self._settings.load().minimum_customer_year,
                cancelled=self._cancel.is_set,
            )
            if self._cancel.is_set():
                raise InterruptedError("Indexlauf abgebrochen.")
            self._set_phase("publishing")
            generation = self._publisher.publish_all()
            components = dict(generation.get("components") or {})
            index_generation = dict(components.get("index") or {})
            customer_generation = dict(components.get("customers") or {})
            with self._lock:
                self._state.state = "completed"
                self._state.phase = "completed"
                self._state.completed_at = utc_now()
                self._state.error = ""
                self._state.queued_action = ""
                self._state.extra = {
                    "recognition": recognition,
                    "index_generation": index_generation["generation"],
                    "customer_generation": customer_generation["generation"],
                }
                self._persist_state()
            return 0
        except InterruptedError:
            with self._lock:
                self._state.state = "cancelled"
                self._state.phase = "cancelled"
                self._state.completed_at = utc_now()
                self._state.queued_action = ""
                self._persist_state()
            return 2
        except Exception:
            logger.error("Indexlauf fehlgeschlagen; technische Diagnose: index_run_failed")
            with self._lock:
                self._state.state = "error"
                self._state.phase = "error"
                self._state.completed_at = utc_now()
                self._state.error = "Indexlauf fehlgeschlagen (index_run_failed)."
                self._state.queued_action = ""
                self._persist_state()
            return 1

    def _progress(self, count: int, path: str) -> None:
        with self._lock:
            self._state.processed_count = count
            self._state.current_path = path
            if count < 10 or count % 25 == 0:
                self._persist_state()

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._state.phase = phase
            self._persist_state()

    def extract_customer_documents(self, project_root_ids: list[int], cancelled) -> None:
        with self.operation_lock:
            if self._source_guard is not None:
                self._source_guard.ensure_ready(self.source_path, self.source_id)
            self._catalog.build(
                self.source_path, source_id=self.source_id, full_rebuild=False,
                settings=self._settings.load(), cancelled=cancelled,
                progress=lambda _count, _path: None, force_extraction=True,
                project_root_ids=project_root_ids,
            )

    def rebuild_recognition_documents(self, cancelled) -> None:
        """Build a fresh catalog and force extraction, retaining the active snapshot on failure."""
        with self.operation_lock:
            if self._source_guard is not None:
                self._source_guard.ensure_ready(self.source_path, self.source_id)
            self._catalog.build(
                self.source_path, source_id=self.source_id, full_rebuild=True,
                settings=self._settings.load(), cancelled=cancelled,
                progress=lambda _count, _path: None, force_extraction=True,
                verify_content=True,
            )

    def _delete_index(self) -> None:
        while not self._stopped.is_set() and not self._restart_requested.is_set():
            if self.operation_lock.acquire(timeout=0.2):
                break
        else:
            return
        try:
            if self._stopped.is_set() or self._restart_requested.is_set():
                return
            self._delete_index_locked()
        finally:
            self.operation_lock.release()

    def _delete_index_locked(self) -> None:
        self._catalog.delete()
        content = self.data_path / "index" / "content"
        if content.exists():
            shutil.rmtree(content)
        self._publisher.delete_index_generations()
        with self._lock:
            self._state = MutableRunState(state="deleted", phase="maintenance")
            self._persist_state()

    def _persist_state(self) -> None:
        payload = self._state.snapshot().to_dict()
        payload.update(self._state.extra)
        if self._run_state is not None:
            self._run_state.save(payload)

    def _recover_persisted_state(self) -> None:
        """Expose interrupted work as resumable instead of claiming it completed."""
        if self._run_state is None or (payload := self._run_state.load()) is None:
            return
        previous_state = str(payload.get("state", ""))
        if previous_state not in {"running", "cancelling"}:
            return
        self._state = MutableRunState(
            run_id=str(payload.get("run_id", "")),
            state="idle",
            phase="resume-pending",
            processed_count=int(payload.get("processed_count", 0)),
            current_path=str(payload.get("current_path", "")),
            full_rebuild=bool(payload.get("full_rebuild", False)),
            started_at=str(payload.get("started_at", "")),
            error="Der vorherige Lauf wurde unterbrochen und wird fortgesetzt.",
        )

    def _generation_backup_counts(self) -> dict[str, int]:
        root = self.data_path / "generations-v2"
        result = {}
        for component in ("index", "customers"):
            count = len(list((root / component / "archives").glob("*.zip")))
            result[component] = max(0, count - 1)
        return result

    def _source_probe(self) -> tuple[bool, str]:
        if self._source_guard is not None:
            return self._source_guard.probe(self.source_path, self.source_id)
        if self.source_path.is_dir():
            return True, ""
        return False, "Datenquelle nicht erreichbar."


class IndexAdminService:
    """Narrow facade exposed by the admin API."""

    def __init__(self, coordinator: IndexRunCoordinator) -> None:
        self._coordinator = coordinator

    def start(self, *, full_rebuild: bool = False) -> dict[str, Any]:
        return self._coordinator.request_run(full_rebuild=full_rebuild)

    def cancel(self) -> dict[str, Any]:
        return self._coordinator.request_cancel()

    def delete(self, *, rebuild: bool = True) -> dict[str, Any]:
        return self._coordinator.request_delete(rebuild=rebuild)

    def restart(self) -> dict[str, Any]:
        return self._coordinator.request_restart()
