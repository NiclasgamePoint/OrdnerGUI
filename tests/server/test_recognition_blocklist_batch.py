"""Atomic exclusion edits use invented customers and temporary SQLite stores only."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import Mock

import httpx
import pytest

from papagui_server.adapters.customer_uow import SqliteCustomerUnitOfWorkFactory
from papagui_server.adapters.recognition_blocklist import SqliteRecognitionBlocklistRepository
from papagui_server.api import create_app
from papagui_server.application.recognition_blocklist import RecognitionBlocklistApplicationService
from papagui_server.composition import RuntimeConfiguration, build_container
from papagui_server.domain.models import MutableRunState


@pytest.fixture
def batch_store(tmp_path):
    factory = SqliteCustomerUnitOfWorkFactory(tmp_path / "synthetic-customers.sqlite")
    factory.initialize()
    with factory() as work:
        work._connection.executemany(
            "INSERT INTO customers(id,folder_path,display_name) VALUES (?,?,?)",
            [(1, "customer://synthetic-one", "Synthetic One"),
             (2, "customer://synthetic-two", "Synthetic Two")],
        )
        for customer_id in (1, 2):
            work.suggestions.add(
                customer_id, kind="phone", value="030 12345678", suggestion_type="field",
                source_path="source://synthetic/evidence.txt", excerpt="Invented evidence",
                fingerprint="", confidence=0, quality="strong", party_role="customer",
                engine_version="synthetic", run_id="one",
            )
        work.commit()
    publisher = Mock(spec=["publish_customers"])
    return factory, publisher, RecognitionBlocklistApplicationService(factory, publisher)


def test_batch_reconciles_and_publishes_once_and_duplicate_retry_does_neither(batch_store, monkeypatch):
    factory, publisher, service = batch_store
    calls = []
    original = SqliteRecognitionBlocklistRepository.apply_pending

    def reconcile(repository, customer_id=None):
        calls.append(customer_id)
        return original(repository, customer_id)

    monkeypatch.setattr(SqliteRecognitionBlocklistRepository, "apply_pending", reconcile)
    additions = [
        {"kind": "phone", "value": "0049 30 12345678", "reason": "Original"},
        {"kind": "phone", "value": "+49 (30) 12345678", "reason": "Duplicate"},
        {"kind": "company", "value": "Synthetic GmbH"},
    ]
    result = service.batch(additions, [])
    assert result["changed"] and result["published"]
    assert len(result["entries"]) == 2
    assert next(entry for entry in result["entries"] if entry["kind"] == "phone")["reason"] == "Original"
    assert calls == [None]
    publisher.publish_customers.assert_called_once_with()
    with factory() as work:
        assert work.suggestions.list_for_customer(1, status="pending") == []
        assert work.suggestions.list_for_customer(2, status="pending") == []
        assert work.blocklist.pending_publication() is None
        assert work._connection.execute("SELECT count(*) FROM candidate_evidence").fetchone()[0] == 2
    retry = service.batch(additions, [987654])
    assert retry == {**result, "changed": False}
    assert calls == [None]
    publisher.publish_customers.assert_called_once_with()


@pytest.mark.parametrize("additions,deletions", [
    ([{"kind": "company", "value": "Synthetic GmbH"}, {"kind": "phone", "value": "bad"}], []),
    ([{"kind": "company", "value": "Synthetic GmbH"}], [0]),
    ([{"kind": "company", "value": "Synthetic GmbH"}], [True]),
    ([{"kind": "company", "value": "Synthetic GmbH"}], [1 << 63]),
    ([{"kind": "company", "value": "Synthetic GmbH"}] * 501, []),
    ([], [1] * 501),
])
def test_batch_validates_every_item_before_any_mutation(batch_store, additions, deletions):
    factory, publisher, _ = batch_store
    with factory() as work:
        existing = work.blocklist.add("phone", "030 12345678")
        work.commit()
    with factory() as work:
        statements = []
        work._connection.set_trace_callback(statements.append)
        with pytest.raises(ValueError):
            work.blocklist.apply_batch(additions, [existing["id"], *deletions])
        assert work.blocklist.list() == [existing]
        assert not any(query.startswith(("INSERT", "DELETE", "UPDATE")) for query in statements)
        assert work.suggestions.list_for_customer(1, status="pending") == []
    publisher.publish_customers.assert_not_called()


def test_reconciliation_failure_rolls_back_entire_delta_and_visibility(batch_store, monkeypatch):
    factory, publisher, service = batch_store
    initial = service.batch([{"kind": "phone", "value": "030 12345678"}], [])
    publisher.reset_mock()

    def fail(repository, customer_id=None):
        repository._connection.execute("UPDATE customer_document_suggestions SET lifecycle='active'")
        raise RuntimeError("Synthetic transaction failure")

    monkeypatch.setattr(SqliteRecognitionBlocklistRepository, "apply_pending", fail)
    with pytest.raises(RuntimeError, match="Synthetic transaction failure"):
        service.batch([{"kind": "company", "value": "Synthetic GmbH"}], [initial["entries"][0]["id"]])
    assert service.list() == initial["entries"]
    with factory() as work:
        assert work.suggestions.list_for_customer(1, status="pending") == []
        assert work.blocklist.pending_publication() is None
    publisher.publish_customers.assert_not_called()


def test_batch_restores_only_relevant_customers_once_before_publication(batch_store, monkeypatch):
    factory, publisher, service = batch_store
    added = service.batch([{"kind": "phone", "value": "030 12345678"}], [])
    with factory() as work:
        work.customers.update(1, {"phone": "+49 30 12345678"}, expected_revision=1)
        work.commit()
    publisher.reset_mock()

    def inspect_published_visibility():
        with factory() as work:
            assert work.suggestions.list_for_customer(1, status="pending") == []
            assert len(work.suggestions.list_for_customer(2, status="pending")) == 1

    publisher.publish_customers.side_effect = inspect_published_visibility
    result = service.batch([], [added["entries"][0]["id"]])
    assert result == {"entries": [], "changed": True, "published": True}
    publisher.publish_customers.assert_called_once_with()
    assert service.batch([], [added["entries"][0]["id"]]) == {
        "entries": [], "changed": False, "published": True,
    }
    publisher.publish_customers.assert_called_once_with()


def test_publication_failure_returns_committed_entries_and_retries_after_service_restart(batch_store):
    factory, publisher, service = batch_store
    assert service.list_state() == {"entries": [], "publication_pending": False}
    publisher.publish_customers.side_effect = OSError("Synthetic publication failure")
    result = service.batch([{"kind": "company", "value": "Synthetic GmbH"}], [])
    assert result["changed"] and not result["published"]
    assert service.list() == result["entries"]
    assert service.list_state() == {"entries": result["entries"], "publication_pending": True}
    publisher.publish_customers.assert_called_once_with()
    restarted_publisher = Mock(spec=["publish_customers"])
    restarted = RecognitionBlocklistApplicationService(factory, restarted_publisher)
    assert restarted.list_state() == {"entries": result["entries"], "publication_pending": True}
    assert restarted.batch([], []) == {**result, "changed": False, "published": True}
    restarted_publisher.publish_customers.assert_called_once_with()
    assert restarted.list_state() == {"entries": result["entries"], "publication_pending": False}
    assert restarted.batch([], []) == {**result, "changed": False, "published": True}
    restarted_publisher.publish_customers.assert_called_once_with()


def test_mixed_batch_retry_and_stale_delete_never_remove_a_new_entry(batch_store):
    factory, publisher, service = batch_store
    # Simulate an upgraded database whose last allocated ID has no metadata yet.
    with factory() as work:
        work._connection.execute(
            "INSERT INTO recognition_blocklist(id,kind,value,normalized_value,reason) "
            "VALUES (42,'company','Synthetic Old GmbH','synthetic old gmbh','')"
        )
        work.commit()
    additions = [{"kind": "company", "value": "Synthetic New GmbH"}]
    result = service.batch(additions, [42])
    assert result["changed"] and result["published"]
    assert len(result["entries"]) == 1 and result["entries"][0]["id"] > 42
    publisher.publish_customers.assert_called_once_with()
    assert service.batch(additions, [42]) == {**result, "changed": False}
    assert service.batch([], [42]) == {**result, "changed": False}
    publisher.publish_customers.assert_called_once_with()


def test_legacy_delete_then_add_also_retains_highest_deleted_id_after_reopen(batch_store):
    factory, publisher, service = batch_store
    with factory() as work:
        work._connection.execute(
            "INSERT INTO recognition_blocklist(id,kind,value,normalized_value,reason) "
            "VALUES (42,'company','Synthetic Old GmbH','synthetic old gmbh','')"
        )
        work.commit()
    service.delete(42)
    restarted = RecognitionBlocklistApplicationService(factory, publisher)
    entry = restarted.add("company", "Synthetic New GmbH")
    assert entry["id"] > 42
    publisher.reset_mock()
    assert restarted.batch([], [42]) == {"entries": [entry], "changed": False, "published": True}
    publisher.publish_customers.assert_not_called()


def test_readding_deleted_canonical_value_wins_and_then_retries_as_noop(batch_store):
    _, publisher, service = batch_store
    previous = service.add("company", "Synthetic GmbH", "Previous")
    publisher.reset_mock()
    additions = [{"kind": "company", "value": "SYNTHETIC GmbH", "reason": "Replacement"}]
    result = service.batch(additions, [previous["id"]])
    entry, = result["entries"]
    assert entry["id"] > previous["id"] and entry["reason"] == "Replacement"
    assert result["changed"] and result["published"]
    assert service.batch(additions, [previous["id"]]) == {**result, "changed": False}
    publisher.publish_customers.assert_called_once_with()


def test_concurrent_deltas_preserve_unrelated_entries_and_publish_each_change_once(batch_store):
    factory, publisher, service = batch_store
    initial = service.batch([
        {"kind": "company", "value": "Synthetic Keep GmbH"},
        {"kind": "company", "value": "Synthetic Remove GmbH"},
    ], [])
    remove_id = next(entry["id"] for entry in initial["entries"] if "Remove" in entry["value"])
    publisher.reset_mock()
    barrier = Barrier(2)

    def edit(value, deletions):
        barrier.wait(timeout=5)
        return service.batch([{"kind": "company", "value": value}], deletions)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(edit, "Synthetic First GmbH", [remove_id])
        second = executor.submit(edit, "Synthetic Second GmbH", [])
        assert first.result(timeout=10)["changed"]
        assert second.result(timeout=10)["changed"]
    assert {entry["value"] for entry in service.list()} == {
        "Synthetic Keep GmbH", "Synthetic First GmbH", "Synthetic Second GmbH",
    }
    assert publisher.publish_customers.call_count == 2
    with factory() as work:
        assert work.blocklist.pending_publication() is None


def test_older_publication_cannot_clear_retry_marker_of_newer_failed_batch(batch_store):
    factory, _, _ = batch_store
    publishing = Event()
    complete = Event()

    def publish_older():
        publishing.set()
        assert complete.wait(timeout=10)

    older = RecognitionBlocklistApplicationService(
        factory, Mock(publish_customers=Mock(side_effect=publish_older)),
    )
    newer_publisher = Mock(publish_customers=Mock(side_effect=OSError("Synthetic failure")))
    newer = RecognitionBlocklistApplicationService(factory, newer_publisher)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(older.batch, [{"kind": "company", "value": "Synthetic First GmbH"}], [])
        try:
            assert publishing.wait(timeout=5)
            result = newer.batch([{"kind": "company", "value": "Synthetic Second GmbH"}], [])
            assert result["changed"] and not result["published"]
        finally:
            complete.set()
        assert first.result(timeout=10)["published"]
    with factory() as work:
        assert work.blocklist.pending_publication() is not None
    newer_publisher.publish_customers.side_effect = None
    assert newer.batch([], []) == {"entries": newer.list(), "changed": False, "published": True}
    assert newer_publisher.publish_customers.call_count == 2


def test_batch_api_auth_validation_capability_and_running_index(tmp_path, monkeypatch):
    async def scenario():
        source = tmp_path / "synthetic-source"
        source.mkdir()
        container = build_container(RuntimeConfiguration(
            source_path=source, data_path=tmp_path / "synthetic-data",
            config_path=tmp_path / "synthetic-config", client_token="synthetic-token",
        ))
        publisher = Mock(spec=["publish_customers"])
        monkeypatch.setattr(container.recognition_blocklist, "_publisher", publisher)
        container.coordinator._state = MutableRunState(state="running", run_id="synthetic-index")
        app = create_app(container, manage_lifecycle=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            endpoint = "/v2/admin/recognition/blocklist/batch"
            assert (await client.post(endpoint, json={})).status_code == 401
            client.headers["Authorization"] = "Bearer synthetic-token"
            capabilities = (await client.get("/v2/system/info")).json()["capabilities"]["features"]
            assert "recognition-blocklist-batch" in capabilities
            payload = {"additions": [{"kind": "company", "value": "Synthetic GmbH"}], "deletions": []}
            response = await client.post(endpoint, json=payload)
            assert response.status_code == 200
            result = response.json()
            assert result["changed"] and result["published"] and len(result["entries"]) == 1
            for bad in (
                {**payload, "deletions": [0]}, {**payload, "deletions": [True]},
                {**payload, "deletions": [1 << 63]},
                {**payload, "deletions": ["1"]}, {**payload, "deletions": [1] * 501},
                {"additions": payload["additions"] * 501},
            ):
                assert (await client.post(endpoint, json=bad)).status_code == 422
            invalid = {"additions": [*payload["additions"], {"kind": "phone", "value": "bad"}],
                       "deletions": [result["entries"][0]["id"]]}
            invalid_response = await client.post(endpoint, json=invalid)
            assert invalid_response.status_code == 400
            assert invalid_response.json()["error"]["message"].startswith("Sperre 2:")
            assert "bad" not in invalid_response.json()["error"]["message"]
            assert container.recognition_blocklist.list() == result["entries"]
            assert (await client.post(endpoint, json=payload)).json() == {**result, "changed": False}
            publisher.publish_customers.assert_called_once_with()
            assert container.coordinator._state.state == "running"
            publisher.publish_customers.side_effect = OSError("Synthetic publication failure")
            changed = await client.post(endpoint, json={"deletions": [result["entries"][0]["id"]]})
            assert changed.json() == {"entries": [], "changed": True, "published": False}
            state = await client.get("/v2/admin/recognition/blocklist")
            assert state.json() == {"entries": [], "publication_pending": True}
            publisher.publish_customers.side_effect = None
            assert (await client.post(endpoint, json={})).json() == {
                "entries": [], "changed": False, "published": True,
            }
            state = await client.get("/v2/admin/recognition/blocklist")
            assert state.json() == {"entries": [], "publication_pending": False}

    asyncio.run(scenario())
