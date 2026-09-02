"""Structured error and optimistic-conflict payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ._base import (
    ContractValidationError,
    JsonDto,
    JsonValue,
    mapping_get,
    optional_int,
    optional_string,
    require_int,
    require_mapping,
    require_string,
)
from .customers import Customer, IdempotencyKey, MutationTarget


@dataclass(frozen=True, slots=True)
class ErrorDetail(JsonDto):
    code: str
    message: str
    field: str | None = None

    def __post_init__(self) -> None:
        require_string(self.code, "code")
        require_string(self.message, "message")
        optional_string(self.field, "field")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ErrorDetail":
        payload = require_mapping(payload, "error_detail")
        return cls(
            code=require_string(mapping_get(payload, "code", "invalid"), "code"),
            message=require_string(mapping_get(payload, "message", "Invalid value"), "message"),
            field=optional_string(mapping_get(payload, "field", None), "field"),
        )


@dataclass(frozen=True, slots=True)
class ApiError(JsonDto):
    code: str
    message: str
    status: int
    details: tuple[ErrorDetail, ...] = ()
    request_id: str | None = None

    def __post_init__(self) -> None:
        require_string(self.code, "code")
        require_string(self.message, "message")
        require_int(self.status, "status", minimum=400, maximum=599)
        details = tuple(self.details)
        if not all(isinstance(detail, ErrorDetail) for detail in details):
            raise ContractValidationError("details must contain ErrorDetail DTOs")
        object.__setattr__(self, "details", details)
        optional_string(self.request_id, "request_id")

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, object],
        *,
        default_status: int = 500,
    ) -> "ApiError":
        payload = require_mapping(payload, "api_error")
        nested = mapping_get(payload, "error", None)
        if isinstance(nested, Mapping):
            payload = require_mapping(nested, "error")
            nested = None
        if isinstance(nested, str):
            # v1 only exposed a human-readable `error` string.
            return cls(
                code="legacy_error",
                message=require_string(nested, "error"),
                status=require_int(
                    mapping_get(payload, "status", default_status),
                    "status",
                    minimum=400,
                    maximum=599,
                ),
                request_id=optional_string(mapping_get(payload, "request_id", None), "request_id"),
            )
        raw_details = mapping_get(payload, "details", ())
        if isinstance(raw_details, (str, bytes, bytearray)) or not isinstance(
            raw_details, (tuple, list)
        ):
            raise ContractValidationError("details must be an array")
        return cls(
            code=require_string(mapping_get(payload, "code", "error"), "code"),
            message=require_string(mapping_get(payload, "message", "Request failed"), "message"),
            status=require_int(
                mapping_get(payload, "status", default_status),
                "status",
                minimum=400,
                maximum=599,
            ),
            details=tuple(
                ErrorDetail.from_dict(require_mapping(item, "details")) for item in raw_details
            ),
            request_id=optional_string(mapping_get(payload, "request_id", None), "request_id"),
        )


@dataclass(frozen=True, slots=True)
class ConflictPayload(JsonDto):
    error: ApiError
    target: MutationTarget
    expected_revision: int
    actual_revision: int | None = None
    current: Customer | None = None
    idempotency_key: IdempotencyKey | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.error, ApiError):
            raise ContractValidationError("error must be an ApiError DTO")
        if self.error.status != 409:
            raise ContractValidationError("conflict error status must be 409")
        if not isinstance(self.target, MutationTarget):
            try:
                object.__setattr__(self, "target", MutationTarget(str(self.target)))
            except ValueError as exc:
                raise ContractValidationError("unknown mutation target") from exc
        require_int(self.expected_revision, "expected_revision", minimum=0)
        optional_int(self.actual_revision, "actual_revision", minimum=0)
        if self.current is not None and not isinstance(self.current, Customer):
            raise ContractValidationError("current must be a Customer DTO")
        if (
            self.current is not None
            and self.actual_revision is not None
            and self.current.revision != self.actual_revision
        ):
            raise ContractValidationError(
                "actual_revision must match the current customer revision"
            )
        if self.idempotency_key is not None and not isinstance(
            self.idempotency_key, IdempotencyKey
        ):
            object.__setattr__(
                self,
                "idempotency_key",
                IdempotencyKey.from_value(self.idempotency_key),
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "error": self.error.to_dict(),
            "target": self.target.value,
            "expected_revision": self.expected_revision,
            "actual_revision": self.actual_revision,
            "current": self.current.to_dict() if self.current is not None else None,
            "idempotency_key": (
                self.idempotency_key.value if self.idempotency_key is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ConflictPayload":
        payload = require_mapping(payload, "conflict")
        raw_error = mapping_get(payload, "error", "Conflict")
        error_payload: Mapping[str, object]
        if isinstance(raw_error, str):
            error_payload = {
                "error": raw_error,
                "status": 409,
                "request_id": mapping_get(payload, "request_id", None),
            }
        else:
            error_payload = require_mapping(raw_error, "error")
        error = ApiError.from_dict(error_payload, default_status=409)
        if error.status != 409:
            raise ContractValidationError("conflict error status must be 409")
        raw_current = mapping_get(payload, "current", None)
        current = (
            Customer.from_dict(require_mapping(raw_current, "current"))
            if raw_current is not None
            else None
        )
        actual_value = mapping_get(payload, "actual_revision", None)
        if actual_value is None and current is not None:
            actual_value = current.revision
        raw_key = mapping_get(payload, "idempotency_key", None)
        try:
            target = MutationTarget(
                require_string(
                    mapping_get(payload, "target", MutationTarget.CUSTOMER.value),
                    "target",
                )
            )
        except ValueError as exc:
            raise ContractValidationError("unknown mutation target") from exc
        return cls(
            error=error,
            target=target,
            expected_revision=require_int(
                mapping_get(payload, "expected_revision", 0),
                "expected_revision",
                minimum=0,
            ),
            actual_revision=optional_int(actual_value, "actual_revision", minimum=0),
            current=current,
            idempotency_key=(IdempotencyKey.from_value(raw_key) if raw_key is not None else None),
        )


ConflictErrorPayload = ConflictPayload
ApiErrorPayload = ApiError
