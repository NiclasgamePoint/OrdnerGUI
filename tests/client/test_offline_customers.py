from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from papagui_contracts import Customer
from papagui_client.adapters.sqlite_customers import SQLiteCustomerOutbox, SQLiteCustomerSnapshot
from papagui_client.application.customers import OfflineFirstCustomerStore
from papagui_client.application.errors import CustomerGatewayConflict, CustomerGatewayUnavailable
from papagui_client.application.models import (
    CustomerMutationKind,
    GatewayMutationResult,
    PendingCustomerMutation,
)
from papagui_client.presentation.coordinators import CustomerCoordinator


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, folder_path TEXT NOT NULL, display_name TEXT NOT NULL,
            entity_type TEXT NOT NULL, company TEXT NOT NULL, email TEXT NOT NULL,
            phone TEXT NOT NULL, street TEXT NOT NULL, postal_code TEXT NOT NULL,
            city TEXT NOT NULL, revision INTEGER NOT NULL
        );
        CREATE TABLE contacts (id INTEGER PRIMARY KEY, customer_id INTEGER, name TEXT, role TEXT, email TEXT, phone TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, customer_id INTEGER, body TEXT);
        CREATE TABLE customer_services (customer_id INTEGER, name TEXT);
        CREATE TABLE customer_folders (customer_id INTEGER, folder_path TEXT);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE customer_tags (customer_id INTEGER, tag_id INTEGER);
        """
    )
    connection.execute(
        "INSERT INTO customers VALUES(1,'customer://one','Muster','Unternehmen','','','','','','Berlin',1)"
    )
    connection.commit()
    connection.close()


class FakeGateway:
    def __init__(self, mode="online"):
        self.mode = mode
        self.calls = []
        self.seen = {}

    def list_customers(self):
        return []

    def get_customer(self, _customer_id):
        return None

    def mutate(self, mutation):
        self.calls.append(mutation.idempotency_key)
        if self.mode == "offline":
            raise CustomerGatewayUnavailable("offline")
        if self.mode == "conflict":
            raise CustomerGatewayConflict(Customer(id=1, revision=2, display_name="Server"))
        if mutation.idempotency_key in self.seen:
            return self.seen[mutation.idempotency_key]
        customer = Customer.from_dict(mutation.payload) if mutation.payload else None
        if customer is not None and customer.id is None:
            customer = Customer.from_dict({**customer.to_dict(), "id": 42, "revision": 1})
        elif customer is not None:
            customer = Customer.from_dict({**customer.to_dict(), "revision": customer.revision + 1})
        result = GatewayMutationResult(customer=customer, deleted=customer is None)
        self.seen[mutation.idempotency_key] = result
        return result


class CommitThenLoseResponseGateway(FakeGateway):
    def __init__(self):
        super().__init__("online")
        self._lose_once = True

    def mutate(self, mutation):
        if mutation.idempotency_key in self.seen:
            self.calls.append(mutation.idempotency_key)
            return self.seen[mutation.idempotency_key]
        result = super().mutate(mutation)
        if self._lose_once:
            self._lose_once = False
            raise CustomerGatewayUnavailable("response lost after commit")
        return result


def store(snapshot_path, outbox_path, gateway):
    return OfflineFirstCustomerStore(
        SQLiteCustomerSnapshot(snapshot_path), SQLiteCustomerOutbox(outbox_path), gateway
    )


def test_offline_edit_uses_overlay_and_never_changes_snapshot(tmp_path):
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    before = sha256(snapshot)
    gateway = FakeGateway("offline")
    customers = store(snapshot, tmp_path / "outbox.db", gateway)
    edited = Customer(id=1, revision=1, display_name="Muster lokal", city="Berlin")

    result = customers.save(edited)

    assert result.queued
    assert result.replay.unavailable
    assert customers.get_customer(1).display_name == "Muster lokal"
    assert sha256(snapshot) == before


def test_replay_is_durable_and_idempotent(tmp_path):
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    outbox = tmp_path / "outbox.db"
    offline = FakeGateway("offline")
    first_process = store(snapshot, outbox, offline)
    first_process.save(Customer(id=1, revision=1, display_name="Neu"))

    online = FakeGateway("online")
    second_process = store(snapshot, outbox, online)
    replay = second_process.replay()
    replay_again = second_process.replay()

    assert replay.applied == 1
    assert replay.remaining == 0
    assert replay_again.applied == 0
    assert len(online.calls) == 1
    assert second_process.get_customer(1).display_name == "Neu"


def test_lost_response_then_second_edit_uses_new_key_and_rebases_revision(tmp_path):
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    gateway = CommitThenLoseResponseGateway()
    customers = store(snapshot, tmp_path / "outbox.db", gateway)

    first = customers.save(Customer(id=1, revision=1, display_name="Erste Fassung"))
    assert first.replay.unavailable
    first_key = gateway.calls[0]
    second = customers.save(Customer(id=1, revision=1, display_name="Zweite Fassung"))

    assert not second.queued
    assert len(set(gateway.calls)) == 2
    assert gateway.calls[:2] == [first_key, first_key]
    second_key = next(key for key in gateway.calls if key != first_key)
    second_call = next(
        item for item in gateway.seen if item == second_key
    )
    assert second_call == second_key
    assert second.customer.display_name == "Zweite Fassung"
    assert second.customer.revision == 3


def test_outbound_customer_mutations_never_persist_absolute_legacy_paths(tmp_path):
    database = tmp_path / "outbox.db"
    outbox = SQLiteCustomerOutbox(database)
    customer = Customer(
        id=1,
        revision=1,
        display_name="Portable",
        folder_path="C:\\Kunden\\Alpha",
        folder_paths=(
            "C:\\Kunden\\Alpha",
            "\\\\nas\\kunden\\Alpha",
            "/Volumes/Kunden/Alpha",
            "/srv/kunden/Alpha",
            "source://primary/Kunden/Alpha",
        ),
    )

    mutation = outbox.enqueue_upsert(customer, "update", 1)
    serialized = json.dumps(mutation.payload)

    assert mutation.payload["folder_path"] == "source://primary/Kunden/Alpha"
    assert mutation.payload["folder_paths"] == ["source://primary/Kunden/Alpha"]
    assert "C:\\\\Kunden" not in serialized
    assert "\\\\\\\\nas" not in serialized
    assert "/Volumes/" not in serialized
    assert "/srv/" not in serialized


def test_snapshot_hydrates_portable_projects_without_touching_database(tmp_path):
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    connection = sqlite3.connect(snapshot)
    connection.execute(
        "CREATE TABLE customer_projects ("
        "id INTEGER PRIMARY KEY,customer_id INTEGER,project_root_id INTEGER,"
        "source_id TEXT,relative_path TEXT,service_type TEXT,project_label TEXT,"
        "project_city TEXT,year INTEGER,provenance TEXT)"
    )
    connection.execute(
        "INSERT INTO customer_projects VALUES(2,1,9,'archive',"
        "'2026/Beratung/Muster','Beratung','Umbau','Berlin',2026,'folder')"
    )
    connection.commit()
    connection.close()
    before = sha256(snapshot)

    customer = SQLiteCustomerSnapshot(snapshot).get_customer(1)

    assert customer.projects[0].source.source_id == "archive"
    assert customer.projects[0].source.relative_path == "2026/Beratung/Muster"
    assert customer.folder_paths == ("source://archive/2026/Beratung/Muster",)
    assert sha256(snapshot) == before


def test_customer_outbox_validation_deduplication_and_reconciliation_edges(tmp_path):
    outbox = SQLiteCustomerOutbox(tmp_path / "outbox.db")
    local = Customer(display_name="Lokal")
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(local, "delete", 0)
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(Customer(id=1), "create", 0)
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(local, "create", 0, aggregate_key="bad")
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(local, "update", 0)
    with pytest.raises(ValueError):
        outbox.discard_local("1")

    first = outbox.enqueue_upsert(Customer(id=1, revision=1, display_name="A"), "update", 1)
    repeated = outbox.enqueue_upsert(
        Customer(id=1, revision=1, display_name="A"), "update", 1
    )
    assert repeated.idempotency_key == first.idempotency_key
    outbox.mark_applied(first, GatewayMutationResult(Customer(id=1, revision=2, display_name="A")))
    assert outbox.overlay_states()["1"] == "awaiting_snapshot"

    class Snapshot:
        def get_customer(self, _customer_id):
            return Customer(id=1, revision=2, display_name="A")

    outbox.reconcile(Snapshot())
    assert outbox.overlay() == {}
    deleted = outbox.enqueue_delete(1, 2)
    assert outbox.enqueue_delete(1, 2).idempotency_key == deleted.idempotency_key
    outbox.mark_applied(deleted, GatewayMutationResult(deleted=True))

    class DeletedSnapshot:
        def get_customer(self, _customer_id):
            return None

    outbox.reconcile(DeletedSnapshot())
    assert outbox.overlay() == {}
    outbox.mark_applied(
        PendingCustomerMutation(
            0,
            "unused-key",
            "local:x",
            CustomerMutationKind.CREATE,
            0,
            {},
        ),
        GatewayMutationResult(),
    )
    orphan = outbox.enqueue_upsert(Customer(display_name="Orphan"), "create", 0)
    outbox.discard_change(orphan.aggregate_key)
    outbox.mark_applied(orphan, GatewayMutationResult(Customer(id=3, revision=1)))
    with pytest.raises(ValueError):
        outbox.discard_change("x", Customer(display_name="unstable"))


def test_customer_outbox_rejects_invalid_legacy_operation_transactionally(tmp_path):
    legacy = tmp_path / "legacy.db"
    connection = sqlite3.connect(legacy)
    connection.execute(
        "CREATE TABLE pending_customer_changes (customer_id INTEGER,operation TEXT,"
        "expected_revision INTEGER,payload_json TEXT,created_at TEXT)"
    )
    connection.execute(
        "INSERT INTO pending_customer_changes VALUES(1,'execute',0,'{}','now')"
    )
    connection.commit()
    connection.close()
    outbox = SQLiteCustomerOutbox(tmp_path / "outbox.db")
    with pytest.raises(ValueError):
        outbox.import_legacy_queue(legacy)


def test_conflict_keeps_local_overlay_and_returns_server_value(tmp_path):
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    customers = store(snapshot, tmp_path / "outbox.db", FakeGateway("conflict"))
    local = Customer(id=1, revision=1, display_name="Client A")

    result = customers.save(local)

    assert result.queued
    assert len(result.replay.conflicts) == 1
    assert result.replay.conflicts[0].local.display_name == "Client A"
    assert result.replay.conflicts[0].current.display_name == "Server"
    assert customers.get_customer(1).display_name == "Client A"


def test_offline_create_has_stable_local_key_and_is_remapped_after_replay(tmp_path):
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    outbox = tmp_path / "outbox.db"
    first = store(snapshot, outbox, FakeGateway("offline"))
    created = first.save(Customer(display_name="Offline neu"))
    assert created.local_key.startswith("local:")
    assert any(item.display_name == "Offline neu" for item in first.list_customers())

    second = store(snapshot, outbox, FakeGateway("online"))
    replay = second.replay()
    assert replay.applied == 1
    assert any(item.id == 42 for item in second.list_customers())


def conflicted_coordinator(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    gateway = FakeGateway("conflict")
    customer_store = store(snapshot, tmp_path / "outbox.db", gateway)
    coordinator = CustomerCoordinator(
        SimpleNamespace(current=SimpleNamespace(customers=customer_store))
    )
    coordinator.save(Customer(id=1, revision=1, display_name="Client A"))
    return coordinator, gateway, coordinator.conflicts()[0]


def test_conflict_workflow_can_reload_or_discard_local_change(tmp_path):
    coordinator, _gateway, case = conflicted_coordinator(tmp_path / "reload")
    current = coordinator.reload_from_server(case)
    assert current.display_name == "Server"
    assert coordinator.list()[0].customer.display_name == "Server"
    assert coordinator.conflicts() == ()

    coordinator, _gateway, case = conflicted_coordinator(tmp_path / "discard")
    coordinator.discard_local(case)
    assert coordinator.list()[0].customer.display_name == "Muster"
    assert coordinator.conflicts() == ()


def test_conflict_workflow_can_retry_or_save_manual_merge(tmp_path):
    coordinator, gateway, case = conflicted_coordinator(tmp_path / "retry")
    gateway.mode = "online"
    retried = coordinator.retry_against_current(case)
    assert retried.customer.display_name == "Client A"
    assert retried.customer.revision == 3
    assert coordinator.conflicts() == ()

    coordinator, gateway, case = conflicted_coordinator(tmp_path / "merge")
    gateway.mode = "online"
    merged = coordinator.save_merge(
        case, Customer(id=1, revision=1, display_name="Zusammengeführt", city="Berlin")
    )
    assert merged.customer.display_name == "Zusammengeführt"
    assert merged.customer.revision == 3
    assert coordinator.conflicts() == ()


def test_customer_delete_conflict_can_retry_delete_at_current_revision(tmp_path):
    snapshot = tmp_path / "customers.db"
    snapshot_database(snapshot)
    gateway = FakeGateway("conflict")
    customer_store = store(snapshot, tmp_path / "outbox.db", gateway)
    coordinator = CustomerCoordinator(
        SimpleNamespace(current=SimpleNamespace(customers=customer_store))
    )

    coordinator.delete(Customer(id=1, revision=1, display_name="Muster"))
    case = coordinator.conflicts()[0]
    assert case.local is None
    gateway.mode = "online"
    retried = coordinator.retry_against_current(case)

    assert not retried.queued
    assert gateway.calls[-1] != case.mutation.idempotency_key
    assert coordinator.conflicts() == ()

def test_legacy_offline_queue_is_imported_once_without_losing_source_rows(tmp_path):
    legacy = tmp_path / "customer-offline-queue.db"
    connection = sqlite3.connect(legacy)
    connection.execute(
        "CREATE TABLE pending_customer_changes ("
        "customer_id INTEGER PRIMARY KEY, operation TEXT NOT NULL, "
        "expected_revision INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    payload = Customer(id=1, revision=2, display_name="Legacy edit").to_dict()
    connection.execute(
        "INSERT INTO pending_customer_changes VALUES(1,'save',2,?,'2026-01-01')",
        (json.dumps(payload),),
    )
    connection.commit()
    connection.close()

    outbox = SQLiteCustomerOutbox(tmp_path / "customer-outbox.db")
    assert outbox.import_legacy_queue(legacy) == 1
    assert outbox.import_legacy_queue(legacy) == 0
    assert outbox.pending()[0].payload["display_name"] == "Legacy edit"
    assert sqlite3.connect(legacy).execute(
        "SELECT COUNT(*) FROM pending_customer_changes"
    ).fetchone()[0] == 1


def test_renamed_legacy_queue_migrates_create_and_delete_in_place(tmp_path):
    database = tmp_path / "customer-outbox.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE pending_customer_changes ("
        "customer_id INTEGER PRIMARY KEY, operation TEXT NOT NULL, "
        "expected_revision INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO pending_customer_changes VALUES(?,?,?,?,?)",
        (
            (
                41,
                "create",
                0,
                json.dumps(Customer(id=41, display_name="Local legacy").to_dict()),
                "2026-01-01",
            ),
            (42, "delete", 3, "{}", "2026-01-02"),
        ),
    )
    connection.commit()
    connection.close()

    outbox = SQLiteCustomerOutbox(database)
    pending = outbox.pending()
    assert [item.operation.value for item in pending] == ["create", "delete"]
    assert pending[0].aggregate_key == "local:legacy-41"
    assert pending[0].payload["id"] is None
    assert outbox.overlay()["42"][0] == "delete"
