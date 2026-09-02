from __future__ import annotations

import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from papagui_contracts import Customer, CustomerJournalEntry
from papagui_client.adapters.http_api import (
    ApiConflictError,
    ApiIdempotencyConflictError,
    ApiRejectedError,
)
from papagui_client.adapters.http_journal import HttpJournalGateway
from papagui_client.adapters.sqlite_customers import SQLiteCustomerSnapshot
from papagui_client.adapters.sqlite_journal_outbox import SQLiteJournalOutbox
from papagui_client.application.errors import (
    CustomerGatewayConflict,
    CustomerGatewayUnavailable,
)
from papagui_client.application.journals import OfflineFirstJournalStore
from papagui_client.application.models import (
    JournalGatewayResult,
    JournalMutationKind,
    PendingJournalMutation,
)
from papagui_client.presentation.coordinators import JournalCoordinator


def _snapshot(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE customers (
          id INTEGER PRIMARY KEY, folder_path TEXT NOT NULL, display_name TEXT NOT NULL,
          entity_type TEXT NOT NULL, company TEXT NOT NULL, email TEXT NOT NULL,
          phone TEXT NOT NULL, street TEXT NOT NULL, postal_code TEXT NOT NULL,
          city TEXT NOT NULL, revision INTEGER NOT NULL
        );
        CREATE TABLE customer_journal_entries (
          id INTEGER PRIMARY KEY, customer_id INTEGER, entry_number INTEGER,
          title TEXT, body TEXT, created_at TEXT, updated_at TEXT
        );
        INSERT INTO customers VALUES(1,'','Muster','Unternehmen','','','','','','',2);
        INSERT INTO customer_journal_entries VALUES(4,1,1,'Alt','Text','a','b');
        """
    )
    connection.commit()
    connection.close()


class JournalGateway:
    def __init__(self, mode="online"):
        self.mode = mode
        self.calls = []
        self.seen = {}

    def list_entries(self, _customer_id):
        return [], 0

    def mutate(self, mutation):
        self.calls.append(mutation)
        if self.mode == "offline":
            raise CustomerGatewayUnavailable("offline")
        if self.mode == "conflict":
            raise CustomerGatewayConflict(Customer(id=1, revision=9, display_name="Server"))
        if mutation.idempotency_key in self.seen:
            return self.seen[mutation.idempotency_key]
        entry = (
            CustomerJournalEntry.from_dict(mutation.payload)
            if mutation.payload
            else None
        )
        if entry is not None and entry.id is None:
            entry = CustomerJournalEntry.from_dict(
                {**entry.to_dict(), "id": 8, "customer_id": 1}
            )
        result = JournalGatewayResult(
            entry,
            mutation.expected_revision + 1,
            mutation.operation is JournalMutationKind.DELETE,
        )
        self.seen[mutation.idempotency_key] = result
        return result


class CommitThenLoseJournalResponse(JournalGateway):
    def __init__(self):
        super().__init__()
        self.lose_once = True

    def mutate(self, mutation):
        if mutation.idempotency_key in self.seen:
            self.calls.append(mutation)
            return self.seen[mutation.idempotency_key]
        result = super().mutate(mutation)
        if self.lose_once:
            self.lose_once = False
            raise CustomerGatewayUnavailable("response lost after commit")
        return result


def _store(snapshot, outbox, gateway):
    return OfflineFirstJournalStore(
        SQLiteCustomerSnapshot(snapshot), SQLiteJournalOutbox(outbox), gateway
    )


def test_journal_overlay_is_offline_and_snapshot_stays_immutable(tmp_path):
    snapshot = tmp_path / "customers.db"
    _snapshot(snapshot)
    before = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    journal = _store(snapshot, tmp_path / "outbox.db", JournalGateway("offline"))

    created = journal.save(
        1, CustomerJournalEntry(title="Lokal", body="Offline"), 2
    )
    edited = journal.save(
        1,
        CustomerJournalEntry(title="Lokal 2", body="Offline"),
        2,
        local_key=created.local_key,
    )

    assert created.queued and edited.queued
    assert [item.entry.title for item in journal.list_entries(1)] == ["Alt", "Lokal 2"]
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before
    pending = SQLiteJournalOutbox(tmp_path / "outbox.db").pending()
    assert len(pending) == 2
    assert pending[0].idempotency_key != pending[1].idempotency_key


def test_journal_replay_remaps_local_key_and_rebases_followup(tmp_path):
    snapshot = tmp_path / "customers.db"
    _snapshot(snapshot)
    outbox = tmp_path / "outbox.db"
    offline = _store(snapshot, outbox, JournalGateway("offline"))
    first = offline.save(1, CustomerJournalEntry(title="A", body="1"), 2)
    offline.save(
        1,
        CustomerJournalEntry(title="B", body="2"),
        2,
        local_key=first.local_key,
    )

    gateway = JournalGateway()
    online = _store(snapshot, outbox, gateway)
    result = online.replay()

    assert result.applied == 2
    assert result.remaining == 0
    assert [call.expected_revision for call in gateway.calls] == [2, 3]
    assert gateway.calls[0].idempotency_key != gateway.calls[1].idempotency_key
    assert online.list_entries(1)[-1].entry.title == "B"


def test_journal_lost_response_then_edit_reuses_old_key_before_new_key(tmp_path):
    snapshot = tmp_path / "customers.db"
    _snapshot(snapshot)
    gateway = CommitThenLoseJournalResponse()
    journal = _store(snapshot, tmp_path / "outbox.db", gateway)

    first = journal.save(
        1, CustomerJournalEntry(id=4, customer_id=1, title="A", body="1"), 2
    )
    assert first.replay.unavailable
    old_key = gateway.calls[0].idempotency_key
    second = journal.save(
        1, CustomerJournalEntry(id=4, customer_id=1, title="B", body="2"), 2
    )

    assert not second.queued
    assert gateway.calls[1].idempotency_key == old_key
    assert gateway.calls[2].idempotency_key != old_key
    assert gateway.calls[2].expected_revision == 3


def test_journal_delete_and_conflict_are_explicit(tmp_path):
    snapshot = tmp_path / "customers.db"
    _snapshot(snapshot)
    journal = _store(snapshot, tmp_path / "outbox.db", JournalGateway("offline"))
    result = journal.delete(1, 4, 2)
    assert result.queued
    assert journal.list_entries(1) == ()

    conflict = _store(
        snapshot, tmp_path / "conflict.db", JournalGateway("conflict")
    ).save(1, CustomerJournalEntry(id=4, title="Neu", body="Text"), 2)
    assert conflict.replay.conflicts[0].current_customer.revision == 9


def _mutation(kind=JournalMutationKind.CREATE):
    entry = CustomerJournalEntry(
        id=None if kind is JournalMutationKind.CREATE else 4,
        customer_id=1,
        title="Titel",
        body="Text",
    )
    return PendingJournalMutation(
        sequence=1,
        idempotency_key="journal-idem-001",
        aggregate_key="local:a" if entry.id is None else str(entry.id),
        customer_id=1,
        operation=kind,
        expected_revision=2,
        target_id=entry.id,
        payload={} if kind is JournalMutationKind.DELETE else entry.to_dict(),
    )


def test_http_journal_gateway_routes_and_conflict_types():
    gateway = HttpJournalGateway("http://server")
    from unittest.mock import Mock

    gateway._transport = Mock()
    gateway._transport.json.return_value = {
        "entries": [CustomerJournalEntry(id=4, customer_id=1, body="A").to_dict()],
        "revision": 2,
    }
    entries, revision = gateway.list_entries(1)
    assert entries[0].id == 4 and revision == 2

    for kind, method, suffix in (
        (JournalMutationKind.CREATE, "POST", "/journal"),
        (JournalMutationKind.UPDATE, "PUT", "/journal/4"),
        (JournalMutationKind.DELETE, "DELETE", "/journal/4"),
    ):
        gateway._transport.json.return_value = {
            "entry": CustomerJournalEntry(id=4, customer_id=1, body="A").to_dict(),
            "revision": 3,
        }
        result = gateway.mutate(_mutation(kind))
        assert gateway._transport.json.call_args.args[0] == method
        assert gateway._transport.json.call_args.args[1].endswith(suffix)
        assert result.revision == 3

    gateway._transport.json.side_effect = ApiRejectedError(
        409,
        "stale",
        {
            "error": {"code": "customer_revision_conflict"},
            "current": Customer(id=1, revision=3).to_dict(),
        },
    )
    with pytest.raises(ApiConflictError):
        gateway.mutate(_mutation())
    gateway._transport.json.side_effect = ApiRejectedError(
        409, "reused", {"error": {"code": "idempotency_conflict"}}
    )
    with pytest.raises(ApiIdempotencyConflictError):
        gateway.mutate(_mutation())


def test_journal_outbox_validation_dedup_apply_reconcile_and_discard(tmp_path):
    outbox = SQLiteJournalOutbox(tmp_path / "outbox.db")
    entry = CustomerJournalEntry(title="A", body="B")
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(1, CustomerJournalEntry(id=4), "create", 1)
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(1, entry, "create", 1, aggregate_key="bad")
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(1, entry, "update", 1)
    with pytest.raises(ValueError):
        outbox.enqueue_upsert(1, entry, "delete", 1)

    created = outbox.enqueue_upsert(1, entry, "create", 1)
    repeated = outbox.enqueue_upsert(
        1, entry, "create", 1, aggregate_key=created.aggregate_key
    )
    assert repeated.idempotency_key == created.idempotency_key
    with pytest.raises(ValueError):
        outbox.mark_applied(created, JournalGatewayResult(None, 2))
    outbox.mark_applied(
        created,
        JournalGatewayResult(
            CustomerJournalEntry(id=8, customer_id=1, title="A", body="B"),
            2,
        ),
    )
    assert outbox.overlay_states()["8"] == "awaiting_snapshot"
    outbox.reconcile(None)
    outbox.reconcile(Customer(display_name="local"))
    outbox.reconcile(Customer(id=1, revision=2))
    assert outbox.overlay() == {}

    updated = outbox.enqueue_upsert(
        1, CustomerJournalEntry(id=8, customer_id=1, title="Neu"), "update", 2
    )
    with pytest.raises(ValueError):
        outbox.mark_applied(updated, JournalGatewayResult(None, 3))
    outbox.discard("8")
    outbox.mark_applied(updated, JournalGatewayResult(None, 3))

    deleted = outbox.enqueue_delete(1, 8, 3)
    assert outbox.enqueue_delete(1, 8, 3).idempotency_key == deleted.idempotency_key
    outbox.mark_applied(deleted, JournalGatewayResult(None, 4, True))
    assert outbox.overlay()["8"][0] == "delete"
    outbox.discard("8")


def test_journal_coordinator_exposes_explicit_conflict_retry_and_discard(tmp_path):
    snapshot = tmp_path / "customers.db"
    _snapshot(snapshot)
    gateway = JournalGateway("conflict")
    store = _store(snapshot, tmp_path / "outbox.db", gateway)
    coordinator = JournalCoordinator(SimpleNamespace(current=SimpleNamespace(journals=store)))

    result = coordinator.save(
        1, CustomerJournalEntry(id=4, customer_id=1, title="Lokal", body="Text"), 2
    )
    assert result.replay.conflicts
    case = coordinator.conflicts()[0]
    gateway.mode = "online"
    retried = coordinator.retry_against_current(case)
    assert not retried.queued
    assert coordinator.conflicts() == ()

    gateway.mode = "conflict"
    coordinator.save(
        1, CustomerJournalEntry(id=4, customer_id=1, title="Noch mal", body="Text"), 3
    )
    case = coordinator.conflicts()[0]
    coordinator.discard_conflict(case)
    assert coordinator.conflicts() == ()


def test_journal_delete_conflict_retries_delete_with_current_customer_revision(tmp_path):
    snapshot = tmp_path / "customers.db"
    _snapshot(snapshot)
    gateway = JournalGateway("conflict")
    store = _store(snapshot, tmp_path / "outbox.db", gateway)
    coordinator = JournalCoordinator(SimpleNamespace(current=SimpleNamespace(journals=store)))

    coordinator.delete(1, 4, 2)
    case = coordinator.conflicts()[0]
    assert case.local is None
    gateway.mode = "online"
    result = coordinator.retry_against_current(case)

    assert not result.queued
    assert gateway.calls[-1].operation is JournalMutationKind.DELETE
    assert gateway.calls[-1].expected_revision == 9
