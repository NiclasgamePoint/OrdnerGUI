"""Durable journal mutation queue sharing the customer offline database."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from typing import Mapping
import uuid

from papagui_contracts import Customer, CustomerJournalEntry

from papagui_client.application.models import (
    JournalGatewayResult,
    JournalMutationKind,
    PendingJournalMutation,
    PendingMutationState,
)

from .sqlite_customer_schema import connect_outbox, initialize_offline_schema


class SQLiteJournalOutbox:
    """Keep journal intent isolated while sequencing by customer revision."""

    def __init__(self, database: Path):
        self._database = database.expanduser().resolve()
        initialize_offline_schema(self._database)

    def enqueue_upsert(
        self,
        customer_id: int,
        entry: CustomerJournalEntry,
        operation: str,
        expected_revision: int,
        aggregate_key: str | None = None,
    ) -> PendingJournalMutation:
        kind = JournalMutationKind(operation)
        if kind is JournalMutationKind.CREATE:
            if entry.id is not None:
                raise ValueError("new journal entries must not have a server id")
            if aggregate_key is not None and not aggregate_key.startswith("local:"):
                raise ValueError("new journal keys must use the local: namespace")
            aggregate_key = aggregate_key or f"local:{uuid.uuid4()}"
            target_id = None
        elif kind is JournalMutationKind.UPDATE:
            if entry.id is None:
                raise ValueError("updated journal entries require a server id")
            aggregate_key = str(entry.id)
            target_id = entry.id
        else:
            raise ValueError("journal upsert operation must be create or update")
        normalized = replace(entry, customer_id=customer_id)
        payload_json = self._entry_json(normalized)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = self._latest_pending(connection, aggregate_key)
            if (
                latest is not None
                and str(latest["operation"]) == kind.value
                and str(latest["payload_json"]) == payload_json
            ):
                connection.commit()
                return self._mutation(latest)
            cursor = connection.execute(
                "INSERT INTO journal_outbox"
                "(idempotency_key,aggregate_key,customer_id,operation,target_id,"
                "expected_revision,payload_json) VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    aggregate_key,
                    customer_id,
                    kind.value,
                    target_id,
                    expected_revision,
                    payload_json,
                ),
            )
            self._write_overlay(
                connection,
                aggregate_key,
                customer_id,
                kind,
                payload_json,
                confirmed=False,
            )
            row = connection.execute(
                "SELECT * FROM journal_outbox WHERE sequence=?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return self._mutation(row)

    def enqueue_delete(
        self, customer_id: int, entry_id: int, expected_revision: int
    ) -> PendingJournalMutation:
        key = str(entry_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = self._latest_pending(connection, key)
            if latest is not None and str(latest["operation"]) == "delete":
                connection.commit()
                return self._mutation(latest)
            cursor = connection.execute(
                "INSERT INTO journal_outbox"
                "(idempotency_key,aggregate_key,customer_id,operation,target_id,"
                "expected_revision,payload_json) VALUES(?,?,?,?,?,?, '{}')",
                (
                    str(uuid.uuid4()),
                    key,
                    customer_id,
                    JournalMutationKind.DELETE.value,
                    entry_id,
                    expected_revision,
                ),
            )
            self._write_overlay(
                connection,
                key,
                customer_id,
                JournalMutationKind.DELETE,
                None,
                confirmed=False,
            )
            row = connection.execute(
                "SELECT * FROM journal_outbox WHERE sequence=?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return self._mutation(row)

    def pending(self) -> list[PendingJournalMutation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM journal_outbox WHERE state='pending' ORDER BY sequence"
            ).fetchall()
        return [self._mutation(row) for row in rows]

    def conflicts(self) -> list[PendingJournalMutation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM journal_outbox WHERE state='conflict' ORDER BY sequence"
            ).fetchall()
        return [self._mutation(row) for row in rows]

    def overlay(
        self,
    ) -> Mapping[str, tuple[str, CustomerJournalEntry | None, bool]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT aggregate_key,operation,payload_json,confirmed FROM journal_overlay"
            ).fetchall()
        return {
            str(row["aggregate_key"]): (
                str(row["operation"]),
                (
                    CustomerJournalEntry.from_dict(json.loads(row["payload_json"]))
                    if row["payload_json"]
                    else None
                ),
                bool(row["confirmed"]),
            )
            for row in rows
        }

    def overlay_for_customer(
        self, customer_id: int
    ) -> Mapping[str, tuple[str, CustomerJournalEntry | None, bool]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT aggregate_key,operation,payload_json,confirmed FROM journal_overlay "
                "WHERE customer_id=?",
                (customer_id,),
            ).fetchall()
        return {
            str(row["aggregate_key"]): (
                str(row["operation"]),
                (
                    CustomerJournalEntry.from_dict(json.loads(row["payload_json"]))
                    if row["payload_json"]
                    else None
                ),
                bool(row["confirmed"]),
            )
            for row in rows
        }

    def overlay_states(self) -> Mapping[str, str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT overlay.aggregate_key,
                       CASE
                         WHEN EXISTS(SELECT 1 FROM journal_outbox queued
                          WHERE queued.aggregate_key=overlay.aggregate_key
                            AND queued.state='conflict') THEN 'conflict'
                         WHEN EXISTS(SELECT 1 FROM journal_outbox queued
                          WHERE queued.aggregate_key=overlay.aggregate_key
                            AND queued.state='pending') THEN 'pending'
                         WHEN overlay.confirmed=1 THEN 'awaiting_snapshot'
                         ELSE 'pending'
                       END AS sync_state
                  FROM journal_overlay overlay
                """
            ).fetchall()
        return {str(row["aggregate_key"]): str(row["sync_state"]) for row in rows}

    def mark_applied(
        self, mutation: PendingJournalMutation, result: JournalGatewayResult
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = connection.execute(
                "SELECT * FROM journal_outbox WHERE sequence=?", (mutation.sequence,)
            ).fetchone()
            if stored is None:
                connection.commit()
                return
            old_key = str(stored["aggregate_key"])
            connection.execute(
                "DELETE FROM journal_outbox WHERE sequence=?", (mutation.sequence,)
            )
            new_key = old_key
            if mutation.operation is JournalMutationKind.CREATE:
                if result.entry is None or result.entry.id is None:
                    raise ValueError("accepted journal creation returned no stable id")
                new_key = str(result.entry.id)
                self._remap_local_chain(
                    connection, old_key, new_key, mutation.customer_id, result.entry
                )
            elif mutation.operation is JournalMutationKind.UPDATE and result.entry is None:
                raise ValueError("accepted journal update returned no entry")
            self._rebase_next(connection, mutation.customer_id, result.revision)
            latest = self._latest_pending(connection, new_key)
            connection.execute(
                "DELETE FROM journal_overlay WHERE aggregate_key IN (?,?)",
                (old_key, new_key),
            )
            if latest is not None:
                kind = JournalMutationKind(str(latest["operation"]))
                self._write_overlay(
                    connection,
                    new_key,
                    mutation.customer_id,
                    kind,
                    None if kind is JournalMutationKind.DELETE else str(latest["payload_json"]),
                    confirmed=False,
                )
            elif mutation.operation is JournalMutationKind.DELETE:
                self._write_overlay(
                    connection,
                    new_key,
                    mutation.customer_id,
                    JournalMutationKind.DELETE,
                    None,
                    confirmed=True,
                )
            elif result.entry is not None:
                self._write_overlay(
                    connection,
                    new_key,
                    mutation.customer_id,
                    JournalMutationKind.UPDATE,
                    self._entry_json(result.entry),
                    confirmed=True,
                )
            connection.execute(
                "UPDATE journal_overlay SET confirmed_revision=? "
                "WHERE aggregate_key=? AND confirmed=1",
                (result.revision, new_key),
            )
            connection.commit()

    def mark_conflict(
        self, mutation: PendingJournalMutation, current: Customer | None
    ) -> None:
        conflict_json = json.dumps(
            current.to_dict() if current is not None else None,
            ensure_ascii=False,
            sort_keys=True,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE journal_outbox SET state='conflict',conflict_json=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE sequence=?",
                (conflict_json, mutation.sequence),
            )
            connection.execute(
                "UPDATE journal_overlay SET conflict_json=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE aggregate_key=?",
                (conflict_json, mutation.aggregate_key),
            )
            connection.commit()

    def count_pending(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM journal_outbox").fetchone()[0]
            )

    def reconcile(self, customer: Customer | None) -> None:
        if customer is None or customer.id is None:
            return
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM journal_overlay WHERE customer_id=? AND confirmed=1 "
                "AND confirmed_revision IS NOT NULL AND confirmed_revision<=?",
                (customer.id, customer.revision),
            )
            connection.commit()

    def discard(self, aggregate_key: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM journal_outbox WHERE aggregate_key=?", (aggregate_key,)
            )
            connection.execute(
                "DELETE FROM journal_overlay WHERE aggregate_key=?", (aggregate_key,)
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return connect_outbox(self._database)

    @staticmethod
    def _entry_json(entry: CustomerJournalEntry) -> str:
        return json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _latest_pending(
        connection: sqlite3.Connection, aggregate_key: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM journal_outbox WHERE aggregate_key=? AND state='pending' "
            "ORDER BY sequence DESC LIMIT 1",
            (aggregate_key,),
        ).fetchone()

    @staticmethod
    def _write_overlay(
        connection: sqlite3.Connection,
        aggregate_key: str,
        customer_id: int,
        kind: JournalMutationKind,
        payload_json: str | None,
        *,
        confirmed: bool,
    ) -> None:
        connection.execute(
            "INSERT INTO journal_overlay"
            "(aggregate_key,customer_id,operation,payload_json,confirmed,"
            "confirmed_revision,conflict_json) VALUES(?,?,?,?,?,NULL,NULL) "
            "ON CONFLICT(aggregate_key) DO UPDATE SET "
            "customer_id=excluded.customer_id,operation=excluded.operation,"
            "payload_json=excluded.payload_json,confirmed=excluded.confirmed,"
            "conflict_json=NULL,updated_at=CURRENT_TIMESTAMP",
            (aggregate_key, customer_id, kind.value, payload_json, int(confirmed)),
        )

    @staticmethod
    def _remap_local_chain(
        connection: sqlite3.Connection,
        old_key: str,
        new_key: str,
        customer_id: int,
        entry: CustomerJournalEntry,
    ) -> None:
        rows = connection.execute(
            "SELECT sequence,operation,payload_json FROM journal_outbox "
            "WHERE aggregate_key=? ORDER BY sequence",
            (old_key,),
        ).fetchall()
        for row in rows:
            kind = JournalMutationKind(str(row["operation"]))
            payload_json = str(row["payload_json"])
            if kind is not JournalMutationKind.DELETE:
                payload = json.loads(payload_json)
                payload["id"] = entry.id
                payload["customer_id"] = customer_id
                payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                kind = JournalMutationKind.UPDATE
            connection.execute(
                "UPDATE journal_outbox SET aggregate_key=?,operation=?,target_id=?,"
                "payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE sequence=?",
                (new_key, kind.value, entry.id, payload_json, int(row["sequence"])),
            )

    @staticmethod
    def _rebase_next(
        connection: sqlite3.Connection, customer_id: int, revision: int
    ) -> None:
        row = connection.execute(
            "SELECT sequence FROM journal_outbox WHERE customer_id=? AND state='pending' "
            "ORDER BY sequence LIMIT 1",
            (customer_id,),
        ).fetchone()
        if row is not None:
            connection.execute(
                "UPDATE journal_outbox SET expected_revision=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE sequence=?",
                (revision, int(row["sequence"])),
            )

    @staticmethod
    def _mutation(row: sqlite3.Row) -> PendingJournalMutation:
        conflict = json.loads(row["conflict_json"]) if row["conflict_json"] else None
        return PendingJournalMutation(
            sequence=int(row["sequence"]),
            idempotency_key=str(row["idempotency_key"]),
            aggregate_key=str(row["aggregate_key"]),
            customer_id=int(row["customer_id"]),
            operation=JournalMutationKind(str(row["operation"])),
            target_id=int(row["target_id"]) if row["target_id"] is not None else None,
            expected_revision=int(row["expected_revision"]),
            payload=json.loads(row["payload_json"]),
            state=PendingMutationState(str(row["state"])),
            conflict=conflict,
        )
