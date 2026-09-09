from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from papagui_server.adapters.run_state import JsonRunStateRepository
from papagui_server.application.indexing import IndexAdminService, IndexRunCoordinator
from papagui_server.domain.errors import ResourceBusyError
from papagui_server.domain.models import MutableRunState, ServerSettings


class Settings:
    def __init__(self, value=None) -> None:
        self.value = value or ServerSettings(automatic_runs_enabled=False)

    def load(self):
        return self.value


class Catalog:
    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.calls: list[bool] = []
        self.deleted = 0
        self.after = None

    def build(self, _source, *, full_rebuild, cancelled, progress, **_kwargs):
        self.calls.append(full_rebuild)
        progress(1, "folder/file.txt")
        progress(10, "../legacy-absolute")
        if self.after:
            self.after()
        if self.mode == "cancel":
            raise InterruptedError("cancelled")
        if self.mode == "error":
            raise RuntimeError("build failed")
        return Path("active.db")

    def delete(self):
        self.deleted += 1


class Recognizer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def synchronize(self, *_args, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("recognition failed")
        return {"detected": 1}


class Publisher:
    def __init__(self) -> None:
        self.value = None
        self.deleted = 0
        self.fail_index = False

    def current(self):
        return self.value

    def publish_index(self):
        if self.fail_index:
            raise RuntimeError("publish failed")
        descriptor = {"generation": "index-one"}
        self.value = {
            "components": {
                "index": descriptor,
                "customers": (self.value or {}).get("components", {}).get("customers"),
            }
        }
        return descriptor

    def publish_customers(self):
        descriptor = {"generation": "customers-one"}
        self.value = {
            "components": {
                "index": (self.value or {}).get("components", {}).get("index"),
                "customers": descriptor,
            }
        }
        return descriptor

    def publish_all(self):
        self.publish_index()
        self.publish_customers()
        return self.value

    def delete_index_generations(self):
        self.deleted += 1


def _coordinator(
    tmp_path: Path,
    *,
    catalog: Catalog | None = None,
    recognizer: Recognizer | None = None,
    publisher: Publisher | None = None,
    settings: Settings | None = None,
    source_exists: bool = True,
):
    source = tmp_path / "source"
    if source_exists:
        source.mkdir(parents=True, exist_ok=True)
    return IndexRunCoordinator(
        source_path=source,
        source_id="primary",
        data_path=tmp_path / "data",
        settings=settings or Settings(),
        catalog=catalog or Catalog(),
        recognizer=recognizer or Recognizer(),
        publisher=publisher or Publisher(),
        run_state=JsonRunStateRepository(
            tmp_path / "data" / "jobs" / "server-status.json"
        ),
    )


def test_success_cancel_error_busy_and_status_paths(tmp_path: Path) -> None:
    catalog = Catalog()
    publisher = Publisher()
    coordinator = _coordinator(tmp_path, catalog=catalog, publisher=publisher)
    assert coordinator.run_once(full_rebuild=True) == 0
    status = coordinator.status()
    assert status["index"]["state"] == "completed"
    assert status["active_index_generation"] == "index-one"
    assert status["active_customer_generation"] == "customers-one"
    assert status["index"]["progress"]["legacy_current_path"] == "../legacy-absolute"
    assert not status["resumable"]

    coordinator._state = MutableRunState(state="running", run_id="busy")
    with pytest.raises(ResourceBusyError):
        coordinator.run_once()
    cancelled = coordinator.request_cancel()
    assert cancelled["run_id"] == "busy"
    with pytest.raises(ResourceBusyError):
        coordinator.request_cancel()

    cancel_catalog = Catalog("cancel")
    cancelled_coordinator = _coordinator(tmp_path / "cancel", catalog=cancel_catalog)
    assert cancelled_coordinator.run_once() == 2
    assert cancelled_coordinator.status()["index"]["state"] == "cancelled"

    failing_publisher = Publisher()
    failing_publisher.fail_index = True
    failed = _coordinator(tmp_path / "failed", publisher=failing_publisher)
    assert failed.run_once() == 1
    assert failed.status()["index"]["message"] == "Indexlauf fehlgeschlagen (index_run_failed)."

    after_catalog = Catalog()
    after_cancel = _coordinator(tmp_path / "after", catalog=after_catalog)
    after_catalog.after = after_cancel._cancel.set
    assert after_cancel.run_once() == 2


def test_admin_requests_delete_restart_and_offline_state(tmp_path: Path) -> None:
    catalog = Catalog()
    publisher = Publisher()
    coordinator = _coordinator(
        tmp_path, catalog=catalog, publisher=publisher, source_exists=False
    )
    admin = IndexAdminService(coordinator)
    first = admin.start(full_rebuild=False)
    second = admin.start(full_rebuild=True)
    assert first["run_id"] == second["run_id"]
    assert not first["queued"]
    coordinator._state.state = "running"
    assert admin.start()["queued"]
    assert admin.delete(rebuild=True) == {"accepted": True, "rebuild": True}
    assert coordinator._cancel.is_set()
    coordinator._state.state = "idle"
    assert admin.delete(rebuild=False) == {"accepted": True, "rebuild": False}
    assert admin.restart() == {"accepted": True, "restart": True}
    assert coordinator.restart_requested
    assert coordinator.wait_for_restart(0)
    assert coordinator.status()["state"] == "stopping"

    degraded = _coordinator(tmp_path / "degraded", source_exists=False)
    assert degraded.status()["state"] == "degraded"
    assert degraded.status()["message"] == "Datenquelle nicht erreichbar."


def test_delete_maintenance_and_backup_counts(tmp_path: Path) -> None:
    catalog = Catalog()
    publisher = Publisher()
    coordinator = _coordinator(tmp_path, catalog=catalog, publisher=publisher)
    content = coordinator.data_path / "index" / "content"
    content.mkdir(parents=True)
    (content / "old").write_text("x", encoding="utf-8")
    for component in ("index", "customers"):
        archives = coordinator.data_path / "generations-v2" / component / "archives"
        archives.mkdir(parents=True)
        for number in range(3):
            (archives / f"{number}.zip").write_bytes(b"zip")
    coordinator._delete_index()
    assert catalog.deleted == 1
    assert publisher.deleted == 1
    assert not content.exists()
    assert coordinator.status()["backups"] == {"index": 2, "customers": 2}
    coordinator._delete_index()


def test_scheduler_handles_manual_run_delete_reset_due_and_idempotent_start(
    tmp_path: Path,
) -> None:
    catalog = Catalog()
    coordinator = _coordinator(tmp_path, catalog=catalog)
    coordinator.start(run_on_start=True)
    coordinator.start(run_on_start=True)
    deadline = time.monotonic() + 2
    while not catalog.calls and time.monotonic() < deadline:
        time.sleep(0.005)
    coordinator.settings_changed(ServerSettings())
    coordinator.stop()
    coordinator.stop()
    assert catalog.calls == [True]

    delete_catalog = Catalog()
    delete_coordinator = _coordinator(tmp_path / "delete", catalog=delete_catalog)
    delete_coordinator.start(run_on_start=False)
    delete_coordinator.request_delete(rebuild=False)
    deadline = time.monotonic() + 2
    while not delete_catalog.deleted and time.monotonic() < deadline:
        time.sleep(0.005)
    delete_coordinator.stop()
    assert delete_catalog.deleted == 1
    assert delete_catalog.calls == []

    due_settings = Settings(
        SimpleNamespace(
            automatic_runs_enabled=True,
            interval_seconds=0,
            minimum_customer_year=2016,
            to_dict=lambda: ServerSettings().to_dict(),
        )
    )
    due_catalog = Catalog()
    due_coordinator = _coordinator(
        tmp_path / "due", catalog=due_catalog, settings=due_settings
    )
    due_catalog.after = due_coordinator._stopped.set
    due_coordinator.start(run_on_start=False)
    deadline = time.monotonic() + 2
    while not due_catalog.calls and time.monotonic() < deadline:
        time.sleep(0.005)
    due_coordinator.stop()
    assert due_catalog.calls == [False]


@pytest.mark.parametrize("previous", ["running", "cancelling"])
def test_interrupted_persisted_state_is_recovered(tmp_path: Path, previous: str) -> None:
    state = tmp_path / "data" / "jobs" / "server-status.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "state": previous,
                "processed_count": 12,
                "current_path": "folder/file.txt",
                "full_rebuild": True,
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    resume = tmp_path / "data" / "index" / "catalog" / "builds" / "resume.db"
    resume.parent.mkdir(parents=True)
    resume.write_bytes(b"checkpoint")
    coordinator = _coordinator(tmp_path)
    status = coordinator.status()
    assert status["index"]["state"] == "idle"
    assert status["index"]["progress"]["phase"] == "resume-pending"
    assert status["resumable"]


def test_invalid_or_completed_persisted_state_is_not_recovered(tmp_path: Path) -> None:
    state = tmp_path / "data" / "jobs" / "server-status.json"
    state.parent.mkdir(parents=True)
    state.write_text("bad json", encoding="utf-8")
    assert _coordinator(tmp_path).status()["index"]["state"] == "idle"
    state.write_text(json.dumps({"state": "completed"}), encoding="utf-8")
    assert _coordinator(tmp_path).status()["index"]["state"] == "idle"
