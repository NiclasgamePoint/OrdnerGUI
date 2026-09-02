"""Offline-first customer use cases with optimistic conflict reporting."""

from __future__ import annotations

from papagui_contracts.customers import Customer

from .errors import CustomerGatewayConflict, CustomerGatewayUnavailable
from .models import (
    CustomerConflict,
    CustomerMutationKind,
    CustomerSyncState,
    CustomerView,
    CustomerWriteResult,
    ReplayResult,
)
from .ports import CustomerGateway, CustomerOutbox, CustomerSnapshot


class OfflineFirstCustomerStore:
    """Overlay durable local intent over an immutable downloaded snapshot."""

    def __init__(
        self,
        snapshot: CustomerSnapshot,
        overlay_outbox: CustomerOutbox,
        gateway: CustomerGateway,
    ) -> None:
        self._snapshot = snapshot
        self._outbox = overlay_outbox
        self._gateway = gateway

    def list_customers(self) -> list[Customer]:
        return [view.customer for view in self.list_customer_views()]

    def list_customer_views(self) -> list[CustomerView]:
        self._outbox.reconcile(self._snapshot)
        customers = {
            str(customer.id): CustomerView(str(customer.id), customer)
            for customer in self._snapshot.list_customers()
            if customer.id is not None
        }
        states = self._outbox.overlay_states()
        for aggregate_key, (operation, customer, _confirmed) in self._outbox.overlay().items():
            if operation == CustomerMutationKind.DELETE.value:
                customers.pop(aggregate_key, None)
            elif customer is not None:
                customers[aggregate_key] = CustomerView(
                    aggregate_key,
                    customer,
                    CustomerSyncState(states.get(aggregate_key, CustomerSyncState.PENDING.value)),
                )
        return sorted(
            customers.values(),
            key=lambda view: (view.customer.display_name.casefold(), view.customer.id or 0),
        )

    def get_customer(self, customer_id: int) -> Customer | None:
        self._outbox.reconcile(self._snapshot)
        entry = self._outbox.overlay().get(str(customer_id))
        if entry is not None:
            operation, customer, _confirmed = entry
            return None if operation == CustomerMutationKind.DELETE.value else customer
        return self._snapshot.get_customer(customer_id)

    def save(
        self,
        customer: Customer,
        expected_revision: int | None = None,
        *,
        local_key: str | None = None,
    ) -> CustomerWriteResult:
        operation = (
            CustomerMutationKind.CREATE
            if customer.id is None
            else CustomerMutationKind.UPDATE
        )
        mutation = self._outbox.enqueue_upsert(
            customer,
            operation.value,
            customer.revision if expected_revision is None else expected_revision,
            aggregate_key=local_key,
        )
        replay = self.replay()
        local = replay.applied_customers.get(mutation.idempotency_key) or self._customer_for_key(
            mutation.aggregate_key
        )
        return CustomerWriteResult(
            customer=local,
            queued=replay.remaining > 0,
            replay=replay,
            local_key=(
                mutation.aggregate_key
                if replay.remaining > 0 and mutation.aggregate_key.startswith("local:")
                else None
            ),
        )

    def delete(self, customer_id: int, expected_revision: int) -> CustomerWriteResult:
        self._outbox.enqueue_delete(customer_id, expected_revision)
        replay = self.replay()
        return CustomerWriteResult(customer=None, queued=replay.remaining > 0, replay=replay)

    def discard_local(self, local_key: str) -> None:
        """Cancel a new customer that has never received a server id."""
        if not local_key.startswith("local:"):
            raise ValueError("only local customer keys can be discarded")
        self._outbox.discard_local(local_key)

    def conflicts(self):
        return self._outbox.conflicts()

    def discard_change(self, aggregate_key: str, current: Customer | None = None) -> None:
        self._outbox.discard_change(aggregate_key, current)

    def fetch_server_customer(
        self, fallback: Customer | None, aggregate_key: str
    ) -> Customer | None:
        try:
            customer_id = int(aggregate_key)
        except ValueError:
            return fallback
        try:
            current = self._gateway.get_customer(customer_id)
            return current if current is not None else fallback
        except CustomerGatewayUnavailable:
            return fallback

    def replay(self) -> ReplayResult:
        applied = 0
        applied_customers = {}
        conflicts: list[CustomerConflict] = []
        unavailable = False
        # Reload the head after every success: applying a predecessor can remap
        # a local id and rebase the next expected revision transactionally.
        while pending := self._outbox.pending():
            mutation = pending[0]
            try:
                result = self._gateway.mutate(mutation)
            except CustomerGatewayConflict as exc:
                local = self._customer_for_key(mutation.aggregate_key)
                self._outbox.mark_conflict(mutation, exc.current)
                conflicts.append(
                    CustomerConflict(
                        idempotency_key=mutation.idempotency_key,
                        local=local,
                        current=exc.current,
                    )
                )
                # Later mutations of the same aggregate depend on the rejected
                # revision and must never be sent speculatively.
                break
            except CustomerGatewayUnavailable:
                unavailable = True
                break
            self._outbox.mark_applied(mutation, result)
            if result.customer is not None:
                applied_customers[mutation.idempotency_key] = result.customer
            applied += 1
        return ReplayResult(
            applied=applied,
            applied_customers=applied_customers,
            conflicts=tuple(conflicts),
            unavailable=unavailable,
            remaining=self._outbox.count_pending(),
        )

    def _customer_for_key(self, aggregate_key: str) -> Customer | None:
        overlay = self._outbox.overlay().get(aggregate_key)
        if overlay is not None:
            return overlay[1]
        try:
            return self._snapshot.get_customer(int(aggregate_key))
        except ValueError:
            return None
