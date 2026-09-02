"""Use cases for document-derived customer suggestions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from papagui_server.application.ports import (
    CustomerUnitOfWorkFactory,
    GenerationPublisherPort,
)
from papagui_server.domain.errors import ResourceNotFoundError
from papagui_server.domain.models import CustomerMutationResult


class CustomerSuggestionApplicationService:
    def __init__(
        self,
        unit_of_work: CustomerUnitOfWorkFactory,
        publisher: GenerationPublisherPort,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._publisher = publisher

    def list_for_customer(
        self, customer_id: int, *, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        with self._unit_of_work() as work:
            customer = work.customers.get(customer_id)
            if customer is None:
                raise ResourceNotFoundError("Kunde nicht gefunden.")
            values = work.suggestions.list_for_customer(customer_id, status=status)
            return values, int(customer["revision"])

    def decide(
        self,
        customer_id: int,
        suggestion_id: int,
        *,
        action: str,
        expected_revision: int | None,
        idempotency_key: str | None,
    ) -> CustomerMutationResult:
        normalized_action = str(action).strip().casefold()
        if normalized_action not in {"accept", "reject"}:
            raise ValueError("Unbekannte Vorschlagsentscheidung.")
        key = (
            idempotency_key
            or (
                f"suggestion-reject-{customer_id}-{suggestion_id}"
                if normalized_action == "reject"
                else ""
            )
        ).strip()
        if not key or len(key) > 200:
            raise ValueError("Ein gültiger Idempotency-Key ist erforderlich.")
        if normalized_action == "accept" and expected_revision is None:
            raise ValueError("expected_revision ist für accept erforderlich.")
        request = {
            "customer_id": customer_id,
            "suggestion_id": suggestion_id,
            "action": normalized_action,
            "expected_revision": expected_revision,
        }
        request_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self._unit_of_work() as work:
            existing = work.customers.idempotency_result(key, request_hash)
            if existing is not None:
                result = CustomerMutationResult(
                    existing.body, existing.status_code, replayed=True
                )
            else:
                current = work.customers.get(customer_id)
                if current is None:
                    raise ResourceNotFoundError("Kunde nicht gefunden.")
                suggestion = work.suggestions.decide(
                    customer_id,
                    suggestion_id,
                    action=normalized_action,
                    expected_revision=expected_revision,
                    current_customer=current,
                )
                saved = work.customers.get(customer_id)
                assert saved is not None
                result = CustomerMutationResult(
                    {"suggestion": suggestion, "customer": saved}, 200
                )
                work.customers.remember_idempotency(
                    key, request_hash, "decide_customer_suggestion", result
                )
                work.commit()
        self._publisher.publish_customers()
        return result
