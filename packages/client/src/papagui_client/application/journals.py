"""Offline-first journal use cases over an immutable customer snapshot."""

from __future__ import annotations

from papagui_contracts import CustomerJournalEntry

from .errors import CustomerGatewayConflict, CustomerGatewayUnavailable
from .models import (
    CustomerSyncState,
    JournalConflict,
    JournalEntryView,
    JournalMutationKind,
    JournalReplayResult,
    JournalWriteResult,
)
from .ports import CustomerSnapshot, JournalGateway, JournalOutbox


class OfflineFirstJournalStore:
    def __init__(
        self,
        snapshot: CustomerSnapshot,
        outbox: JournalOutbox,
        gateway: JournalGateway,
    ) -> None:
        self._snapshot = snapshot
        self._outbox = outbox
        self._gateway = gateway

    def list_entries(self, customer_id: int) -> tuple[JournalEntryView, ...]:
        customer = self._snapshot.get_customer(customer_id)
        self._outbox.reconcile(customer)
        entries = {
            str(entry.id): JournalEntryView(str(entry.id), entry)
            for entry in (customer.journal_entries if customer is not None else ())
            if entry.id is not None
        }
        states = self._outbox.overlay_states()
        for key, (operation, entry, _confirmed) in self._outbox.overlay_for_customer(
            customer_id
        ).items():
            if operation == JournalMutationKind.DELETE.value:
                entries.pop(key, None)
            elif entry is not None:
                entries[key] = JournalEntryView(
                    key,
                    entry,
                    CustomerSyncState(states.get(key, CustomerSyncState.PENDING.value)),
                )
        return tuple(
            sorted(
                entries.values(),
                key=lambda item: (
                    item.entry.entry_number if item.entry.entry_number > 0 else 2**31,
                    item.entry.id or 0,
                    item.key,
                ),
            )
        )

    def save(
        self,
        customer_id: int,
        entry: CustomerJournalEntry,
        expected_revision: int,
        *,
        local_key: str | None = None,
    ) -> JournalWriteResult:
        kind = (
            JournalMutationKind.CREATE
            if entry.id is None
            else JournalMutationKind.UPDATE
        )
        mutation = self._outbox.enqueue_upsert(
            customer_id,
            entry,
            kind.value,
            expected_revision,
            aggregate_key=local_key,
        )
        replay = self.replay()
        view = next(
            (item for item in self.list_entries(customer_id) if item.key == mutation.aggregate_key),
            None,
        )
        return JournalWriteResult(
            entry=(
                replay.applied_entries.get(mutation.idempotency_key)
                or (view.entry if view is not None else entry)
            ),
            queued=replay.remaining > 0,
            replay=replay,
            local_key=(
                mutation.aggregate_key
                if replay.remaining and mutation.aggregate_key.startswith("local:")
                else None
            ),
        )

    def delete(
        self, customer_id: int, entry_id: int, expected_revision: int
    ) -> JournalWriteResult:
        self._outbox.enqueue_delete(customer_id, entry_id, expected_revision)
        replay = self.replay()
        return JournalWriteResult(None, replay.remaining > 0, replay)

    def replay(self) -> JournalReplayResult:
        applied = 0
        applied_entries = {}
        conflicts: list[JournalConflict] = []
        unavailable = False
        while pending := self._outbox.pending():
            mutation = pending[0]
            try:
                result = self._gateway.mutate(mutation)
            except CustomerGatewayConflict as exc:
                local = self._entry_for_key(mutation.customer_id, mutation.aggregate_key)
                self._outbox.mark_conflict(mutation, exc.current)
                conflicts.append(
                    JournalConflict(mutation.idempotency_key, local, exc.current)
                )
                break
            except CustomerGatewayUnavailable:
                unavailable = True
                break
            self._outbox.mark_applied(mutation, result)
            if result.entry is not None:
                applied_entries[mutation.idempotency_key] = result.entry
            applied += 1
        return JournalReplayResult(
            applied=applied,
            applied_entries=applied_entries,
            conflicts=tuple(conflicts),
            unavailable=unavailable,
            remaining=self._outbox.count_pending(),
        )

    def discard(self, aggregate_key: str) -> None:
        self._outbox.discard(aggregate_key)

    def conflicts(self):
        return self._outbox.conflicts()

    def _entry_for_key(
        self, customer_id: int, aggregate_key: str
    ) -> CustomerJournalEntry | None:
        overlay = self._outbox.overlay_for_customer(customer_id).get(aggregate_key)
        if overlay is not None:
            return overlay[1]
        customer = self._snapshot.get_customer(customer_id)
        if customer is None:
            return None
        return next(
            (entry for entry in customer.journal_entries if str(entry.id) == aggregate_key),
            None,
        )
