"""Client-token-authorized review operations."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
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


@dataclass(frozen=True, slots=True)
class SuggestionPage:
    suggestions: tuple[CustomerSuggestion, ...]
    revision: int
    total: int
    has_more: bool = False
    recognition: dict = field(default_factory=dict)
    offline: bool = False


class HttpReviewGateway:
    """Read recognition data and apply revisioned customer suggestions."""

    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 10, *, snapshot=None):
        self._transport = _HttpTransport(server_url, token, timeout_seconds)
        self._snapshot = snapshot

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
        reason: str = "",
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
                    **({"reason": reason[:240]} if reason else {}),
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

    def customer_suggestions_page(
        self, customer_id: int, status: str = "pending", *, limit: int = 30, offset: int = 0
    ) -> SuggestionPage:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid suggestion page")
        query = urllib.parse.urlencode({"status": status, "limit": limit, "offset": offset, "include_groups": "true"})
        try:
            response = self._transport.json("GET", f"/v2/customers/{customer_id}/suggestions?{query}")
        except (ApiUnavailableError, OSError):
            if self._snapshot is None:
                raise
            return self._snapshot.customer_suggestions_page(customer_id, status, limit=limit, offset=offset)
        if not isinstance(response.get("suggestions"), (list, tuple)):
            raise ApiUnavailableError("server returned an invalid suggestion page")
        suggestions = tuple(CustomerSuggestion.from_dict(item) for item in response["suggestions"])
        total = int(response.get("total", len(suggestions)))
        if "total" not in response and "has_more" not in response:
            # Older servers return their complete list and ignore page parameters.
            suggestions = suggestions[offset:offset + limit]
        recognition = response.get("recognition")
        return SuggestionPage(
            suggestions=suggestions,
            revision=int(response.get("revision", 0)),
            total=total,
            has_more=bool(response.get("has_more", offset + len(suggestions) < total)),
            recognition=dict(recognition) if isinstance(recognition, Mapping) else {},
        )

    def recognition_status(self, customer_id: int) -> dict:
        response = self._transport.json("GET", f"/v2/customers/{customer_id}/recognition-status")
        return dict(response)

    def start_customer_recognition(self, customer_id: int, mode: str = "reassess") -> dict:
        if mode not in {"reassess", "extract"}:
            raise ValueError("unknown recognition mode")
        return dict(self._transport.json(
            "POST", f"/v2/customers/{customer_id}/recognition", {"mode": mode}
        ))
