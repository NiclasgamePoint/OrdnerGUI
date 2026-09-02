"""HTTP gateway for revisioned customer journal mutations."""

from __future__ import annotations

from papagui_contracts import (
    Customer,
    CustomerJournalEntry,
    IdempotencyKey,
    MutationEnvelope,
    MutationOperation,
    MutationTarget,
)

from papagui_client.application.models import (
    JournalGatewayResult,
    JournalMutationKind,
    PendingJournalMutation,
)

from .http_api import (
    ApiConflictError,
    ApiIdempotencyConflictError,
    ApiRejectedError,
    _HttpTransport,
)


class HttpJournalGateway:
    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 10):
        self._transport = _HttpTransport(server_url, token, timeout_seconds)

    def list_entries(
        self, customer_id: int
    ) -> tuple[list[CustomerJournalEntry], int]:
        response = self._transport.json(
            "GET", f"/v2/customers/{customer_id}/journal"
        )
        values = response.get("entries", ())
        return (
            [
                CustomerJournalEntry.from_dict(item)
                for item in values
                if isinstance(item, dict)
            ],
            int(response.get("revision", 0)),
        )

    def mutate(self, mutation: PendingJournalMutation) -> JournalGatewayResult:
        headers = {
            "Idempotency-Key": mutation.idempotency_key,
            "If-Match": str(mutation.expected_revision),
        }
        operation = MutationOperation(mutation.operation.value)
        entry = (
            CustomerJournalEntry.from_dict(mutation.payload)
            if mutation.operation is not JournalMutationKind.DELETE
            else None
        )
        envelope = MutationEnvelope(
            operation=operation,
            target=MutationTarget.JOURNAL,
            idempotency_key=IdempotencyKey(mutation.idempotency_key),
            expected_revision=mutation.expected_revision,
            target_id=mutation.target_id,
            customer_id=mutation.customer_id,
            payload=entry,
        )
        if mutation.operation is JournalMutationKind.CREATE:
            method = "POST"
            path = f"/v2/customers/{mutation.customer_id}/journal"
        elif mutation.operation is JournalMutationKind.UPDATE:
            method = "PUT"
            path = (
                f"/v2/customers/{mutation.customer_id}/journal/{mutation.target_id}"
            )
        else:
            method = "DELETE"
            path = (
                f"/v2/customers/{mutation.customer_id}/journal/{mutation.target_id}"
            )
        try:
            response = self._transport.json(method, path, envelope.to_dict(), headers)
        except ApiRejectedError as exc:
            if exc.status != 409:
                raise
            error = exc.payload.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if code == "idempotency_conflict":
                raise ApiIdempotencyConflictError(exc.payload) from exc
            if code not in {None, "customer_revision_conflict"}:
                raise
            current = exc.payload.get("current")
            raise ApiConflictError(
                Customer.from_dict(current) if isinstance(current, dict) else None,
                exc.payload,
            ) from exc
        raw_entry = response.get("entry")
        return JournalGatewayResult(
            entry=(
                CustomerJournalEntry.from_dict(raw_entry)
                if isinstance(raw_entry, dict)
                else None
            ),
            revision=int(response.get("revision", mutation.expected_revision)),
            deleted=mutation.operation is JournalMutationKind.DELETE,
        )
