"""Transactional customer and journal use cases."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from papagui_server.application.ports import (
    CustomerUnitOfWorkFactory,
    GenerationPublisherPort,
)
from papagui_server.domain.models import CustomerMutationResult


class CustomerApplicationService:
    """The sole orchestration boundary for customer mutations.

    Repositories never publish generations themselves. A successful transaction is
    committed first, then the immutable customer snapshot is refreshed. Idempotent
    replays may safely refresh the same snapshot again after a previous publication
    failure.
    """

    def __init__(
        self,
        unit_of_work: CustomerUnitOfWorkFactory,
        publisher: GenerationPublisherPort,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._publisher = publisher

    def list_customers(self, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 2_000 or offset < 0:
            raise ValueError("Ungültige Seiteneinteilung.")
        with self._unit_of_work() as work:
            return work.customers.list(limit=limit, offset=offset)

    def get_customer(self, customer_id: int) -> dict[str, Any]:
        with self._unit_of_work() as work:
            customer = work.customers.get(customer_id)
        if customer is None:
            from papagui_server.domain.errors import ResourceNotFoundError

            raise ResourceNotFoundError("Kunde nicht gefunden.")
        return customer

    def create_customer(
        self, customer: dict[str, Any], *, idempotency_key: str
    ) -> CustomerMutationResult:
        return self._mutate(
            operation="create_customer",
            idempotency_key=idempotency_key,
            request={"customer": customer},
            mutation=lambda repository: CustomerMutationResult(
                {"customer": repository.create(customer)}, 201
            ),
        )

    def update_customer(
        self,
        customer_id: int,
        customer: dict[str, Any],
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CustomerMutationResult:
        return self._mutate(
            operation="update_customer",
            idempotency_key=idempotency_key,
            request={
                "customer_id": customer_id,
                "customer": customer,
                "expected_revision": expected_revision,
            },
            mutation=lambda repository: CustomerMutationResult(
                {
                    "customer": repository.update(
                        customer_id, customer, expected_revision
                    )
                },
                200,
            ),
        )

    def delete_customer(
        self,
        customer_id: int,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CustomerMutationResult:
        def delete(repository: Any) -> CustomerMutationResult:
            repository.delete(customer_id, expected_revision)
            return CustomerMutationResult({"deleted": True, "customer_id": customer_id}, 200)

        return self._mutate(
            operation="delete_customer",
            idempotency_key=idempotency_key,
            request={
                "customer_id": customer_id,
                "expected_revision": expected_revision,
            },
            mutation=delete,
        )

    def add_journal_entry(
        self,
        customer_id: int,
        entry: dict[str, Any],
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CustomerMutationResult:
        def add(repository: Any) -> CustomerMutationResult:
            saved, revision = repository.add_journal(
                customer_id, entry, expected_revision
            )
            return CustomerMutationResult(
                {"entry": saved, "revision": revision}, 201
            )

        return self._mutate(
            operation="add_journal",
            idempotency_key=idempotency_key,
            request={
                "customer_id": customer_id,
                "entry": entry,
                "expected_revision": expected_revision,
            },
            mutation=add,
        )

    def update_journal_entry(
        self,
        customer_id: int,
        entry_id: int,
        entry: dict[str, Any],
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CustomerMutationResult:
        def update(repository: Any) -> CustomerMutationResult:
            saved, revision = repository.update_journal(
                customer_id, entry_id, entry, expected_revision
            )
            return CustomerMutationResult({"entry": saved, "revision": revision}, 200)

        return self._mutate(
            operation="update_journal",
            idempotency_key=idempotency_key,
            request={
                "customer_id": customer_id,
                "entry_id": entry_id,
                "entry": entry,
                "expected_revision": expected_revision,
            },
            mutation=update,
        )

    def delete_journal_entry(
        self,
        customer_id: int,
        entry_id: int,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CustomerMutationResult:
        def delete(repository: Any) -> CustomerMutationResult:
            revision = repository.delete_journal(
                customer_id, entry_id, expected_revision
            )
            return CustomerMutationResult(
                {"deleted": True, "entry_id": entry_id, "revision": revision}, 200
            )

        return self._mutate(
            operation="delete_journal",
            idempotency_key=idempotency_key,
            request={
                "customer_id": customer_id,
                "entry_id": entry_id,
                "expected_revision": expected_revision,
            },
            mutation=delete,
        )

    def _mutate(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request: dict[str, Any],
        mutation: Any,
    ) -> CustomerMutationResult:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise ValueError("Ein gültiger Idempotency-Key ist erforderlich.")
        request_hash = hashlib.sha256(
            json.dumps(
                request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        replayed = False
        with self._unit_of_work() as work:
            existing = work.customers.idempotency_result(key, request_hash)
            if existing is not None:
                result = CustomerMutationResult(
                    body=existing.body,
                    status_code=existing.status_code,
                    replayed=True,
                )
                replayed = True
            else:
                result = mutation(work.customers)
                work.customers.remember_idempotency(
                    key, request_hash, operation, result
                )
                work.commit()

        # Publishing is deliberately outside the database transaction. SQLite
        # backup opens a second read connection and can now observe a complete state.
        self._publisher.publish_customers()
        return result if not replayed else CustomerMutationResult(
            result.body, result.status_code, replayed=True
        )
