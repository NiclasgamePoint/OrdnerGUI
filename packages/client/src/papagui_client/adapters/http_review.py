"""Client-token-authorized review operations."""

from __future__ import annotations

import urllib.parse
from typing import Mapping
import uuid

from papagui_contracts import Customer
from papagui_contracts.recognition import CustomerSuggestion, RecognitionCase

from .http_api import (
    ApiConflictError,
    ApiIdempotencyConflictError,
    ApiRejectedError,
    ApiUnavailableError,
    _HttpTransport,
)


class HttpReviewGateway:
    """Read recognition data and apply revisioned customer suggestions."""

    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 10):
        self._transport = _HttpTransport(server_url, token, timeout_seconds)

    def recognition_cases(self, status: str = "pending") -> tuple[RecognitionCase, ...]:
        query = urllib.parse.urlencode({"status": status}) if status else ""
        response = self._transport.json(
            "GET", "/v2/recognition/cases" + (f"?{query}" if query else "")
        )
        return tuple(
            RecognitionCase.from_dict(item)
            for item in response.get("cases", ())
            if isinstance(item, dict)
        )

    def customer_suggestions(
        self, customer_id: int, status: str = "pending"
    ) -> tuple[tuple[CustomerSuggestion, ...], int]:
        query = urllib.parse.urlencode({"status": status}) if status else ""
        response = self._transport.json(
            "GET",
            f"/v2/customers/{customer_id}/suggestions" + (f"?{query}" if query else ""),
        )
        return (
            tuple(
                CustomerSuggestion.from_dict(item)
                for item in response.get("suggestions", ())
                if isinstance(item, dict)
            ),
            int(response.get("revision", 0)),
        )

    def decide_suggestion(
        self,
        customer_id: int,
        suggestion_id: int,
        action: str,
        expected_revision: int,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[CustomerSuggestion, Customer]:
        key = idempotency_key or str(uuid.uuid4())
        try:
            response = self._transport.json(
                "POST",
                f"/v2/customers/{customer_id}/suggestions/{suggestion_id}/decision",
                {
                    "action": action,
                    "expected_revision": expected_revision,
                    "idempotency_key": key,
                },
                {
                    "Idempotency-Key": key,
                    "If-Match": str(expected_revision),
                },
            )
        except ApiRejectedError as exc:
            if exc.status != 409:
                raise
            error = exc.payload.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            if code == "idempotency_conflict":
                raise ApiIdempotencyConflictError(exc.payload) from exc
            current = exc.payload.get("current")
            raise ApiConflictError(
                Customer.from_dict(current) if isinstance(current, Mapping) else None,
                exc.payload,
            ) from exc
        suggestion = response.get("suggestion")
        customer = response.get("customer")
        if not isinstance(suggestion, Mapping) or not isinstance(customer, Mapping):
            raise ApiUnavailableError("server returned an invalid suggestion decision")
        return CustomerSuggestion.from_dict(suggestion), Customer.from_dict(customer)
