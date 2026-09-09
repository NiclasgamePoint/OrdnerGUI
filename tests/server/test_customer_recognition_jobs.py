"""Customer recheck jobs and HTTP behavior using temporary synthetic data only."""

import asyncio
from copy import deepcopy
import json
import threading
from unittest.mock import Mock

import httpx
import pytest

from papagui_client.adapters.http_review import HttpReviewGateway
from papagui_server.api import create_app
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.application.recognition_jobs import CustomerRecognitionJobs
from papagui_server.domain.errors import ResourceBusyError, ResourceNotFoundError
from tests.server.test_api import _container


class MemoryStore:
    def __init__(self, payload=None):
        self.payload = deepcopy(payload)

    def load(self):
        return deepcopy(self.payload)

    def save(self, value):
        self.payload = deepcopy(value)


def jobs(*, store=None, execute=None, operation_lock=None, limit=100, validate=None,
         document_worker_status=None):
    return CustomerRecognitionJobs(
        store=store or MemoryStore(),
        operation_lock=operation_lock or threading.RLock(),
        validate=validate or (lambda customer_id: None),
        execute=execute or (lambda customer_id, mode, cancelled: None),
        queue_limit=limit,
        document_worker_status=document_worker_status,
    )


@pytest.mark.parametrize("customer_id,mode", [(7, "extract"), (None, "rebuild"), (7, "reassess")])
def test_only_running_extraction_jobs_include_live_document_workers(customer_id, mode):
    store = MemoryStore()
    workers = {"state": "running", "active_workers": 2, "worker_limit": 4}
    snapshots = []
    worker = jobs(store=store, document_worker_status=lambda: workers)
    worker._execute = lambda *_args: snapshots.append(worker.latest(customer_id))
    worker.request(customer_id, mode=mode)
    assert "document_workers" not in worker.latest(customer_id)
    worker.run_pending()
    assert ("document_workers" in snapshots[0]) == (mode in {"extract", "rebuild"})
    if "document_workers" in snapshots[0]:
        assert snapshots[0]["document_workers"] == workers
    assert "document_workers" not in worker.latest(customer_id)
    assert "document_workers" not in store.payload["jobs"][0]


def test_running_job_does_not_inherit_completed_catalog_counts():
    snapshots = []
    worker = jobs(document_worker_status=lambda: {"state": "completed", "processed_documents": 90})
    worker._execute = lambda *_args: snapshots.append(worker.latest(7))
    worker.request(7, mode="extract")
    worker.run_pending()
    assert "document_workers" not in snapshots[0]


def test_job_deduplication_queue_limit_and_defensive_snapshots():
    worker = jobs(limit=2)
    first = worker.request(7)
    assert worker.request(7)["id"] == first["id"]
    second = worker.request(7, mode="extract")
    assert second["id"] != first["id"]
    first["state"] = "tampered"
    assert worker.request(7)["state"] == "queued"
    with pytest.raises(ResourceBusyError):
        worker.request(8)
    assert worker.latest(9) is None
    worker.cancel(7, first["id"])
    third = worker.request(8)
    assert worker.latest(7)["id"] == second["id"]
    assert worker.latest(8)["id"] == third["id"]
    with pytest.raises(ResourceNotFoundError):
        worker.cancel(8, second["id"])
    with pytest.raises(ValueError):
        worker.request(7, mode="unknown")
    invalid = jobs(validate=Mock(side_effect=ResourceNotFoundError("missing")))
    with pytest.raises(ResourceNotFoundError):
        invalid.request(999)


def test_running_job_is_deduplicated_and_cooperatively_cancelled():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def execute(customer_id, mode, cancelled):
        calls.append((customer_id, mode))
        entered.set()
        assert release.wait(2)
        assert cancelled()
        raise InterruptedError

    worker = jobs(execute=execute)
    pending = worker.request(7)
    thread = threading.Thread(target=worker.run_pending)
    thread.start()
    try:
        assert entered.wait(2)
        assert worker.request(7)["id"] == pending["id"]
        assert worker.latest(7)["state"] == "running"
        worker.cancel(7, pending["id"])
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    final = worker.latest(7)
    assert final["state"] == "cancelled" and final["finished_at"] and not final["error"]
    assert worker.cancel(7, pending["id"]) == final
    assert calls == [(7, "reassess")]


def test_shared_writer_lock_keeps_shutdown_responsive_and_work_resumable():
    lock = threading.Lock()
    store = MemoryStore()
    execute = Mock()
    worker = jobs(store=store, operation_lock=lock, execute=execute)
    worker.request(7)
    with lock:
        worker.start()
        first_thread = worker._thread
        worker.start()
        assert worker._thread is first_thread
        worker.stop(timeout=1)
        assert worker._thread is None
    assert not execute.called
    assert worker.latest(7)["state"] == "queued"
    resumed = jobs(store=store, execute=execute)
    resumed.run_pending()
    assert resumed.latest(7)["state"] == "completed" and execute.call_count == 1


def test_start_restarts_a_worker_that_finished_after_shutdown_timeout():
    entered, release, resumed = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def execute(*_args):
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        else:
            resumed.set()

    worker = jobs(execute=execute)
    worker.request(7)
    worker.start()
    try:
        assert entered.wait(2)
        old_thread = worker._thread
        worker.stop(timeout=0)
        release.set()
        old_thread.join(2)
        assert not old_thread.is_alive()
        assert worker.latest(7)["state"] == "queued"
        worker.start()
        assert worker._thread is not old_thread
        assert resumed.wait(2)
    finally:
        release.set()
        worker.stop(timeout=2)


def test_shutdown_during_execution_requeues_and_recovery_discards_invalid_entries():
    store = MemoryStore()
    worker = jobs(store=store)
    worker._execute = lambda *_args: worker.stop(timeout=0)
    identifier = worker.request(7)["id"]
    worker.run_pending()
    assert worker.latest(7)["state"] == "queued"
    assert worker.latest(7)["finished_at"] == ""
    store.payload["jobs"][0]["state"] = "running"
    store.payload["jobs"].extend(
        [None, {"id": "invalid"}, {**store.payload["jobs"][0], "mode": "invalid"}]
    )
    resumed = jobs(store=store)
    assert len(store.payload["jobs"]) == 1
    assert resumed.latest(7)["id"] == identifier
    resumed.run_pending()
    assert resumed.latest(7)["state"] == "completed"


def test_parser_errors_never_persist_exception_text_and_cancelled_queue_never_executes():
    store = MemoryStore()
    execute = Mock(side_effect=RuntimeError("synthetic-private-document-excerpt"))
    worker = jobs(store=store, execute=execute)
    cancelled = worker.request(8)
    worker.cancel(8, cancelled["id"])
    worker.request(7)
    worker.run_pending()
    assert execute.call_count == 1
    assert worker.latest(7)["state"] == "error"
    assert worker.latest(7)["error"] == "recognition_failed"
    assert "synthetic-private-document-excerpt" not in json.dumps(store.payload)
    assert worker.latest(8)["state"] == "cancelled"


def test_running_cancel_survives_restart_before_parser_acknowledges_it():
    store = MemoryStore()
    execute = Mock()
    worker = jobs(store=store, execute=execute)
    pending = worker.request(7)
    # Model a process that dies after cancellation is saved but before its
    # running parser reaches the next cancellation boundary.
    worker._jobs[0]["state"] = "running"
    response = worker.cancel(7, pending["id"])
    assert response["state"] == "running"
    assert "cancelled_job_ids" not in response
    assert store.payload["cancelled_job_ids"] == [pending["id"]]
    recovered = jobs(store=store, execute=execute)
    assert recovered.latest(7)["state"] == "cancelled"
    recovered.run_pending()
    execute.assert_not_called()


def seed(container):
    factory = SqliteCustomerUnitOfWorkFactory(container.configuration.data_path / "customers.db")
    with factory() as work:
        customer = work.customers.create({"display_name": "Synthetic GmbH"})
        work.commit()
    return customer


def test_customer_job_api_auth_validation_status_dedup_cancel_and_errors(tmp_path):
    async def scenario():
        container = _container(tmp_path)
        (container.configuration.source_path / "seed.txt").write_text(
            "Synthetic initialization", encoding="utf-8"
        )
        assert container.coordinator.run_once(full_rebuild=True) == 0
        customer = seed(container)
        customer_id = customer["id"]
        app = create_app(container, manage_lifecycle=False)
        endpoint = f"/v2/customers/{customer_id}/recognition"
        headers = {"Authorization": "Bearer client-token-123"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get(endpoint + "-status")).status_code == 401
            assert (await client.post(endpoint, json={})).status_code == 401
            assert (await client.delete(endpoint + "/missing")).status_code == 401
            assert (
                await client.post(endpoint, headers=headers, json={"mode": "unknown"})
            ).status_code == 400
            assert (
                await client.post("/v2/customers/999/recognition", headers=headers, json={})
            ).status_code == 404
            initial = await client.get(endpoint + "-status", headers=headers)
            assert initial.status_code == 200 and initial.json()["state"] == "not_evaluated"
            queued = await client.post(endpoint, headers=headers, json={"mode": "reassess"})
            assert queued.status_code == 202
            job = queued.json()["job"]
            assert job["state"] == "queued" and job["mode"] == "reassess"
            duplicate = await client.post(endpoint, headers=headers, json={"mode": "reassess"})
            assert duplicate.json()["job"]["id"] == job["id"]
            running = (await client.get(endpoint + "-status", headers=headers)).json()
            assert running["state"] == "running" and running["job"]["id"] == job["id"]
            cancelled = await client.delete(endpoint + "/" + job["id"], headers=headers)
            assert cancelled.json()["job"]["state"] == "cancelled"
            assert (await client.get(endpoint + "-status", headers=headers)).json()[
                "state"
            ] == "partial"
            assert (await client.delete(endpoint + "/missing", headers=headers)).status_code == 404
            container.recognition_jobs._execute = Mock(
                side_effect=RuntimeError("synthetic-private-text")
            )
            await client.post(endpoint, headers=headers, json={"mode": "extract"})
            container.recognition_jobs.run_pending()
            error = (await client.get(endpoint + "-status", headers=headers)).json()
            assert error["state"] == "error" and error["job"]["error"] == "recognition_failed"
            assert "synthetic-private-text" not in json.dumps(error)
            container.recognition_jobs._limit = 1
            await client.post(endpoint, headers=headers, json={"mode": "reassess"})
            assert (
                await client.post(endpoint, headers=headers, json={"mode": "extract"})
            ).status_code == 409

    asyncio.run(scenario())


def test_extract_job_rereads_only_requested_customer_and_publishes_generations(
    tmp_path, monkeypatch
):
    container = _container(tmp_path)
    project = container.configuration.source_path / "Service" / "2026" / "Synthetic GmbH"
    project.mkdir(parents=True)
    document = project / "contact.txt"
    document.write_text("Kunde: Synthetic GmbH\nE-Mail: first@example.org", encoding="utf-8")
    assert container.coordinator.run_once(full_rebuild=True) == 0
    factory = SqliteCustomerUnitOfWorkFactory(container.configuration.data_path / "customers.db")
    with factory() as work:
        customer_id = work.customers.list()[0]["id"]
    root_ids = container.recognition.customer_project_root_ids(customer_id)
    assert root_ids
    build = Mock(wraps=container.coordinator._catalog.build)
    monkeypatch.setattr(container.coordinator._catalog, "build", build)
    document.write_text("Kunde: Synthetic GmbH\nE-Mail: second@example.org", encoding="utf-8")
    container.recognition_jobs.request(customer_id, mode="reassess")
    container.recognition_jobs.run_pending()
    assert not build.called
    assert container.recognition_jobs.latest(customer_id)["state"] == "completed"
    before = container.suggestions.page(customer_id, status="pending", include_groups=True)
    assert "first@example.org" in {item["value"] for item in before["suggestions"]}
    container.recognition_jobs.request(customer_id, mode="extract")
    container.recognition_jobs.run_pending()
    assert build.call_args.kwargs["force_extraction"] is True
    assert build.call_args.kwargs["project_root_ids"] == root_ids
    assert container.recognition_jobs.latest(customer_id)["state"] == "completed"
    after = container.suggestions.page(customer_id, status="pending", include_groups=True)
    assert "second@example.org" in {item["value"] for item in after["suggestions"]}
    assert "first@example.org" not in {item["value"] for item in after["suggestions"]}
    assert container.suggestions.recognition_status(customer_id)["state"] == "complete"


def test_paginated_group_api_and_gateway_keep_legacy_clients_compatible(tmp_path):
    async def scenario():
        container = _container(tmp_path)
        customer = seed(container)
        factory = SqliteCustomerUnitOfWorkFactory(
            container.configuration.data_path / "customers.db"
        )
        with factory() as work:
            for index in range(3):
                work.suggestions.add(
                    customer["id"],
                    kind="email",
                    value=f"person{index}@example.org",
                    source_path=f"source://primary/{index}.txt",
                    excerpt="Synthetic text",
                    fingerprint=f"synthetic-{index}",
                    confidence=0,
                    quality="strong",
                    party_role="customer",
                )
            work.suggestions.add(
                customer["id"],
                kind="address",
                value="Synthetic Straße 1, 12345 Teststadt",
                source_path="source://primary/address.txt",
                excerpt="Synthetic address",
                fingerprint="synthetic-address",
                confidence=0,
                suggestion_type="address",
                payload={
                    "street": "Synthetic Straße 1",
                    "postal_code": "12345",
                    "city": "Teststadt",
                },
            )
            work.commit()
        app = create_app(container, manage_lifecycle=False)
        headers = {"Authorization": "Bearer client-token-123"}
        endpoint = f"/v2/customers/{customer['id']}/suggestions"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            legacy = (await client.get(endpoint, headers=headers)).json()
            assert len(legacy["suggestions"]) == 3
            first = await client.get(
                endpoint,
                headers=headers,
                params={"include_groups": "true", "limit": 2, "offset": 0},
            )
            assert first.status_code == 200
            assert first.json()["total"] == 4 and first.json()["has_more"]
            second = (
                await client.get(
                    endpoint,
                    headers=headers,
                    params={"include_groups": "true", "limit": 2, "offset": 2},
                )
            ).json()
            assert not second["has_more"]
            ids = {item["id"] for item in first.json()["suggestions"]}
            assert not ids.intersection(item["id"] for item in second["suggestions"])
            gateway = HttpReviewGateway("http://synthetic")
            gateway._transport = Mock()
            gateway._transport.json.return_value = second
            page = gateway.customer_suggestions_page(customer["id"], limit=2, offset=2)
            assert page.total == 4 and len(page.suggestions) == 2
            assert any(item.suggestion_type == "address" for item in page.suggestions)
            assert (
                await client.get(endpoint, headers=headers, params={"limit": 501})
            ).status_code == 422

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "job_state,finished,last_run,expected",
    [
        ("cancelled", "2026-09-09T10:00:00+02:00", "2026-09-09T08:01:00Z", "complete"),
        ("error", "2026-09-09 08:00:00", "2026-09-09T08:01:00+00:00", "complete"),
        ("cancelled", "2026-09-09T08:02:00Z", "2026-09-09T08:01:00+00:00", "partial"),
        ("error", "2026-09-09T08:02:00Z", "", "error"),
        ("error", "unknown", "2026-09-09T08:01:00Z", "error"),
    ],
)
def test_later_successful_index_run_supersedes_terminal_job_status(
    tmp_path, monkeypatch, job_state, finished, last_run, expected
):
    async def scenario():
        container = _container(tmp_path)
        customer = seed(container)
        customer_id = customer["id"]
        monkeypatch.setattr(
            container.suggestions,
            "recognition_status",
            lambda _customer_id: {
                "customer_id": customer_id,
                "state": "complete",
                "reason": "no_candidates",
                "summary": "Synthetic successful evaluation",
                "last_run_at": last_run,
            },
        )
        monkeypatch.setattr(
            container.recognition_jobs,
            "latest",
            lambda _customer_id: {
                "id": "synthetic-job",
                "customer_id": customer_id,
                "state": job_state,
                "mode": "reassess",
                "created_at": "2026-09-09T08:00:00Z",
                "finished_at": finished,
                "error": "recognition_failed" if job_state == "error" else "",
            },
        )
        app = create_app(container, manage_lifecycle=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/v2/customers/{customer_id}/recognition-status",
                headers={"Authorization": "Bearer client-token-123"},
            )
        assert response.status_code == 200
        assert response.json()["state"] == expected
        assert response.json()["job"]["state"] == job_state
        if expected == "complete":
            assert response.json()["summary"] == "Synthetic successful evaluation"

    asyncio.run(scenario())
