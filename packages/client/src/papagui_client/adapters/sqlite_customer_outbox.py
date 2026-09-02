"""Durable customer intent queue and materialized local overlay."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Mapping
import uuid

from papagui_contracts import Customer

from papagui_client.application.models import (
    CustomerMutationKind,
    GatewayMutationResult,
    PendingCustomerMutation,
)
from papagui_client.application.ports import CustomerSnapshot

from .sqlite_customer_mapping import (
    customer_from_json,
    customer_json,
    mutation_from_row,
    portable_customer_payload,
)
from .sqlite_customer_schema import connect_outbox, initialize_offline_schema


class SQLiteCustomerOutbox:
    """Persist each semantic mutation under its own immutable idempotency key."""

    def __init__(self, database: Path):
        self._database = database.expanduser().resolve()
        initialize_offline_schema(self._database)
        self.import_legacy_queue(self._database)

    def import_legacy_queue(self, database: Path) -> int:
        """Import the 0.4.1 queue once without mutating its source rows."""
        source = database.expanduser().resolve()
        if not source.is_file():
            return 0
        marker = "legacy-customer-queue-v1:" + hashlib.sha256(
            str(source).encode("utf-8", errors="replace")
        ).hexdigest()
        with closing(self._connect()) as destination:
            if destination.execute(
                "SELECT 1 FROM client_migrations WHERE name=?", (marker,)
            ).fetchone():
                return 0
            same_database = source == self._database
            legacy = destination if same_database else sqlite3.connect(
                f"file:{source.as_posix()}?mode=ro", uri=True
            )
            legacy.row_factory = sqlite3.Row
            try:
                table = legacy.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='pending_customer_changes'"
                ).fetchone()
                if table is None:
                    destination.execute(
                        "INSERT INTO client_migrations(name) VALUES(?)", (marker,)
                    )
                    destination.commit()
                    return 0
                rows = legacy.execute(
                    "SELECT customer_id,operation,expected_revision,payload_json,created_at "
                    "FROM pending_customer_changes ORDER BY created_at,customer_id"
                ).fetchall()
                destination.execute("BEGIN IMMEDIATE")
                imported = 0
                for row in rows:
                    kind = self._legacy_kind(str(row["operation"]))
                    customer_id = int(row["customer_id"])
                    aggregate_key = (
                        f"local:legacy-{customer_id}"
                        if kind is CustomerMutationKind.CREATE
                        else str(customer_id)
                    )
                    if destination.execute(
                        "SELECT 1 FROM customer_outbox WHERE aggregate_key=?",
                        (aggregate_key,),
                    ).fetchone():
                        continue
                    payload_json: str
                    overlay_payload: str | None
                    if kind is CustomerMutationKind.DELETE:
                        payload_json, overlay_payload = "{}", None
                    else:
                        customer = portable_customer_payload(
                            Customer.from_dict(json.loads(str(row["payload_json"])))
                        )
                        if kind is CustomerMutationKind.CREATE:
                            customer = replace(customer, id=None)
                        payload_json = overlay_payload = customer_json(customer)
                    idempotency_key = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{source}:{customer_id}:{row['created_at']}",
                        )
                    )
                    destination.execute(
                        "INSERT INTO customer_outbox"
                        "(idempotency_key,aggregate_key,operation,expected_revision,payload_json) "
                        "VALUES(?,?,?,?,?)",
                        (
                            idempotency_key,
                            aggregate_key,
                            kind.value,
                            int(row["expected_revision"]),
                            payload_json,
                        ),
                    )
                    self._write_overlay(
                        destination,
                        aggregate_key,
                        kind,
                        overlay_payload,
                        confirmed=False,
                    )
                    imported += 1
                destination.execute(
                    "INSERT INTO client_migrations(name) VALUES(?)", (marker,)
                )
                destination.commit()
                return imported
            except Exception:
                destination.rollback()
                raise
            finally:
                if not same_database:
                    legacy.close()

    def enqueue_upsert(
        self,
        customer: Customer,
        operation: str,
        expected_revision: int,
        aggregate_key: str | None = None,
    ) -> PendingCustomerMutation:
        kind = CustomerMutationKind(operation)
        if kind not in {CustomerMutationKind.CREATE, CustomerMutationKind.UPDATE}:
            raise ValueError("customer upsert operation must be create or update")
        if kind is CustomerMutationKind.CREATE:
            if customer.id is not None:
                raise ValueError("new customers must not already have a server id")
            if aggregate_key is not None and not aggregate_key.startswith("local:"):
                raise ValueError("new customer aggregate keys must use the local: namespace")
            aggregate_key = aggregate_key or f"local:{uuid.uuid4()}"
        else:
            if customer.id is None:
                raise ValueError("updated customers require a server id")
            aggregate_key = str(customer.id)
        sanitized = portable_customer_payload(customer)
        payload_json = customer_json(sanitized)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = self._latest_pending(connection, aggregate_key)
            if (
                latest is not None
                and str(latest["operation"]) == kind.value
                and str(latest["payload_json"]) == payload_json
            ):
                connection.commit()
                return mutation_from_row(latest)
            idempotency_key = str(uuid.uuid4())
            cursor = connection.execute(
                "INSERT INTO customer_outbox"
                "(idempotency_key,aggregate_key,operation,expected_revision,payload_json) "
                "VALUES(?,?,?,?,?)",
                (idempotency_key, aggregate_key, kind.value, expected_revision, payload_json),
            )
            self._write_overlay(
                connection, aggregate_key, kind, payload_json, confirmed=False
            )
            row = connection.execute(
                "SELECT * FROM customer_outbox WHERE sequence=?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return mutation_from_row(row)

    def enqueue_delete(
        self, customer_id: int, expected_revision: int
    ) -> PendingCustomerMutation:
        aggregate_key = str(customer_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = self._latest_pending(connection, aggregate_key)
            if latest is not None and str(latest["operation"]) == "delete":
                connection.commit()
                return mutation_from_row(latest)
            cursor = connection.execute(
                "INSERT INTO customer_outbox"
                "(idempotency_key,aggregate_key,operation,expected_revision,payload_json) "
                "VALUES(?,?,?,?, '{}')",
                (
                    str(uuid.uuid4()),
                    aggregate_key,
                    CustomerMutationKind.DELETE.value,
                    expected_revision,
                ),
            )
            self._write_overlay(
                connection,
                aggregate_key,
                CustomerMutationKind.DELETE,
                None,
                confirmed=False,
            )
            row = connection.execute(
                "SELECT * FROM customer_outbox WHERE sequence=?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return mutation_from_row(row)

    def pending(self) -> list[PendingCustomerMutation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM customer_outbox WHERE state='pending' ORDER BY sequence"
            ).fetchall()
        return [mutation_from_row(row) for row in rows]

    def conflicts(self) -> list[PendingCustomerMutation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM customer_outbox WHERE state='conflict' ORDER BY sequence"
            ).fetchall()
        return [mutation_from_row(row) for row in rows]

    def overlay(self) -> Mapping[str, tuple[str, Customer | None, bool]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT aggregate_key,operation,payload_json,confirmed FROM customer_overlay"
            ).fetchall()
        return {
            str(row["aggregate_key"]): (
                str(row["operation"]),
                customer_from_json(row["payload_json"]),
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
                         WHEN EXISTS(
                           SELECT 1 FROM customer_outbox queued
                            WHERE queued.aggregate_key=overlay.aggregate_key
                              AND queued.state='conflict'
                         ) THEN 'conflict'
                         WHEN EXISTS(
                           SELECT 1 FROM customer_outbox queued
                            WHERE queued.aggregate_key=overlay.aggregate_key
                              AND queued.state='pending'
                         ) THEN 'pending'
                         WHEN overlay.confirmed=1 THEN 'awaiting_snapshot'
                         ELSE 'pending'
                       END AS sync_state
                  FROM customer_overlay overlay
                """
            ).fetchall()
        return {str(row["aggregate_key"]): str(row["sync_state"]) for row in rows}

    def mark_applied(
        self, mutation: PendingCustomerMutation, result: GatewayMutationResult
    ) -> None:
        if mutation.sequence == 0:
            return
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = connection.execute(
                "SELECT * FROM customer_outbox WHERE sequence=?", (mutation.sequence,)
            ).fetchone()
            if stored is None:
                connection.commit()
                return
            old_key = str(stored["aggregate_key"])
            connection.execute(
                "DELETE FROM customer_outbox WHERE sequence=?", (mutation.sequence,)
            )
            customer = result.customer
            new_key = old_key
            if mutation.operation is CustomerMutationKind.CREATE:
                if customer is None or customer.id is None:
                    raise ValueError("accepted customer creation returned no stable id")
                new_key = str(customer.id)
                self._remap_local_chain(connection, old_key, new_key, customer)
            elif mutation.operation is CustomerMutationKind.UPDATE:
                if customer is None or customer.id is None:
                    raise ValueError("accepted customer update returned no stable id")
            self._rebase_next(connection, new_key, customer)
            next_row = self._latest_pending(connection, new_key)
            connection.execute(
                "DELETE FROM customer_overlay WHERE aggregate_key IN (?,?)",
                (old_key, new_key),
            )
            if next_row is not None:
                latest_kind = CustomerMutationKind(str(next_row["operation"]))
                latest_payload = (
                    None
                    if latest_kind is CustomerMutationKind.DELETE
                    else str(next_row["payload_json"])
                )
                self._write_overlay(
                    connection, new_key, latest_kind, latest_payload, confirmed=False
                )
            elif mutation.operation is CustomerMutationKind.DELETE:
                self._write_overlay(
                    connection,
                    new_key,
                    CustomerMutationKind.DELETE,
                    None,
                    confirmed=True,
                )
            elif customer is not None:
                self._write_overlay(
                    connection,
                    new_key,
                    CustomerMutationKind.UPDATE,
                    customer_json(customer),
                    confirmed=True,
                )
            connection.commit()

    def mark_conflict(
        self, mutation: PendingCustomerMutation, current: Customer | None
    ) -> None:
        conflict_json = json.dumps(
            current.to_dict() if current is not None else None,
            ensure_ascii=False,
            sort_keys=True,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE customer_outbox SET state='conflict',conflict_json=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE sequence=?",
                (conflict_json, mutation.sequence),
            )
            connection.execute(
                "UPDATE customer_overlay SET conflict_json=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE aggregate_key=?",
                (conflict_json, mutation.aggregate_key),
            )
            connection.commit()

    def reconcile(self, snapshot: CustomerSnapshot) -> None:
        overlays = self.overlay()
        with closing(self._connect()) as connection:
            for key, (operation, customer, confirmed) in overlays.items():
                if not confirmed:
                    continue
                try:
                    remote = snapshot.get_customer(int(key))
                except ValueError:
                    remote = None
                caught_up = (
                    operation == CustomerMutationKind.DELETE.value and remote is None
                ) or (
                    customer is not None
                    and remote is not None
                    and remote.revision >= customer.revision
                )
                if caught_up:
                    connection.execute(
                        "DELETE FROM customer_overlay WHERE aggregate_key=?", (key,)
                    )
            connection.commit()

    def count_pending(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM customer_outbox").fetchone()[0]
            )

    def discard_local(self, aggregate_key: str) -> None:
        if not aggregate_key.startswith("local:"):
            raise ValueError("only local customer keys can be discarded")
        self.discard_change(aggregate_key)

    def discard_change(
        self, aggregate_key: str, current: Customer | None = None
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM customer_outbox WHERE aggregate_key=?", (aggregate_key,)
            )
            connection.execute(
                "DELETE FROM customer_overlay WHERE aggregate_key=?", (aggregate_key,)
            )
            if current is not None:
                if current.id is None:
                    raise ValueError("server customer requires an id")
                self._write_overlay(
                    connection,
                    str(current.id),
                    CustomerMutationKind.UPDATE,
                    customer_json(current),
                    confirmed=True,
                )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return connect_outbox(self._database)

    @staticmethod
    def _latest_pending(
        connection: sqlite3.Connection, aggregate_key: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM customer_outbox WHERE aggregate_key=? AND state='pending' "
            "ORDER BY sequence DESC LIMIT 1",
            (aggregate_key,),
        ).fetchone()

    @staticmethod
    def _write_overlay(
        connection: sqlite3.Connection,
        aggregate_key: str,
        kind: CustomerMutationKind,
        payload_json: str | None,
        *,
        confirmed: bool,
    ) -> None:
        connection.execute(
            "INSERT INTO customer_overlay"
            "(aggregate_key,operation,payload_json,confirmed,conflict_json) "
            "VALUES(?,?,?,?,NULL) ON CONFLICT(aggregate_key) DO UPDATE SET "
            "operation=excluded.operation,payload_json=excluded.payload_json,"
            "confirmed=excluded.confirmed,conflict_json=NULL,updated_at=CURRENT_TIMESTAMP",
            (aggregate_key, kind.value, payload_json, int(confirmed)),
        )

    @staticmethod
    def _remap_local_chain(
        connection: sqlite3.Connection,
        old_key: str,
        new_key: str,
        customer: Customer,
    ) -> None:
        rows = connection.execute(
            "SELECT sequence,operation,payload_json FROM customer_outbox "
            "WHERE aggregate_key=? ORDER BY sequence",
            (old_key,),
        ).fetchall()
        for row in rows:
            kind = CustomerMutationKind(str(row["operation"]))
            payload_json = str(row["payload_json"])
            if kind is not CustomerMutationKind.DELETE:
                payload = json.loads(payload_json)
                payload["id"] = customer.id
                payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                kind = CustomerMutationKind.UPDATE
            connection.execute(
                "UPDATE customer_outbox SET aggregate_key=?,operation=?,payload_json=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE sequence=?",
                (new_key, kind.value, payload_json, int(row["sequence"])),
            )

    @staticmethod
    def _rebase_next(
        connection: sqlite3.Connection,
        aggregate_key: str,
        current: Customer | None,
    ) -> None:
        if current is None:
            return
        row = connection.execute(
            "SELECT sequence,operation,payload_json FROM customer_outbox "
            "WHERE aggregate_key=? AND state='pending' ORDER BY sequence LIMIT 1",
            (aggregate_key,),
        ).fetchone()
        if row is None:
            return
        payload_json = str(row["payload_json"])
        if str(row["operation"]) != CustomerMutationKind.DELETE.value:
            payload = json.loads(payload_json)
            payload["id"] = current.id
            payload["revision"] = current.revision
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        connection.execute(
            "UPDATE customer_outbox SET expected_revision=?,payload_json=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE sequence=?",
            (current.revision, payload_json, int(row["sequence"])),
        )

    @staticmethod
    def _legacy_kind(operation: str) -> CustomerMutationKind:
        try:
            return {
                "create": CustomerMutationKind.CREATE,
                "save": CustomerMutationKind.UPDATE,
                "delete": CustomerMutationKind.DELETE,
            }[operation]
        except KeyError as exc:
            raise ValueError(f"unsupported legacy customer operation: {operation}") from exc
