"""Persistent customer-recognition review contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from ._base import (
    ContractValidationError,
    JsonDto,
    mapping_get,
    optional_int,
    require_int,
    require_mapping,
    require_string,
    string_tuple,
)
from .generations import SourcePath
from .customers import Contact


class RecognitionCaseStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    STALE = "stale"


class RecognitionDecisionAction(str, Enum):
    ACCEPT = "accept"
    ASSIGN = "assign"
    REJECT = "reject"


class CustomerSuggestionStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CustomerSuggestionDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RecognitionEvidence(JsonDto):
    field_name: str
    value: str
    source: SourcePath
    excerpt: str = ""
    rule: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        require_string(self.field_name, "field_name")
        require_string(self.value, "value")
        if not isinstance(self.source, SourcePath):
            raise ContractValidationError("source must be a SourcePath DTO")
        require_string(self.excerpt, "excerpt", allow_empty=True)
        require_string(self.rule, "rule", allow_empty=True)
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise ContractValidationError("confidence must be between 0 and 1")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "RecognitionEvidence":
        payload = require_mapping(payload, "recognition_evidence")
        confidence = mapping_get(payload, "confidence", 0.0)
        return cls(
            field_name=require_string(mapping_get(payload, "field_name", ""), "field_name"),
            value=require_string(mapping_get(payload, "value", ""), "value"),
            source=SourcePath.from_dict(require_mapping(mapping_get(payload, "source", payload), "source")),
            excerpt=require_string(mapping_get(payload, "excerpt", ""), "excerpt", allow_empty=True),
            rule=require_string(mapping_get(payload, "rule", ""), "rule", allow_empty=True),
            confidence=float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else confidence,
        )


@dataclass(frozen=True, slots=True)
class RecognitionCase(JsonDto):
    signature: str
    recognition_key: str
    display_name: str
    project_roots: tuple[SourcePath, ...]
    cities: tuple[str, ...] = ()
    service_types: tuple[str, ...] = ()
    years: tuple[int, ...] = ()
    reason: str = ""
    suggested_customer_ids: tuple[int, ...] = ()
    evidence: tuple[RecognitionEvidence, ...] = ()
    status: RecognitionCaseStatus = RecognitionCaseStatus.PENDING

    def __post_init__(self) -> None:
        for name in ("signature", "recognition_key", "display_name"):
            require_string(getattr(self, name), name)
        roots = tuple(self.project_roots)
        if not roots or not all(isinstance(item, SourcePath) for item in roots):
            raise ContractValidationError("project_roots must contain SourcePath DTOs")
        object.__setattr__(self, "project_roots", roots)
        for name in ("cities", "service_types"):
            object.__setattr__(self, name, string_tuple(getattr(self, name), name))
        years = tuple(require_int(item, "years", minimum=1900) for item in self.years)
        object.__setattr__(self, "years", years)
        ids = tuple(require_int(item, "suggested_customer_ids", minimum=1) for item in self.suggested_customer_ids)
        object.__setattr__(self, "suggested_customer_ids", ids)
        evidence = tuple(self.evidence)
        if not all(isinstance(item, RecognitionEvidence) for item in evidence):
            raise ContractValidationError("evidence must contain RecognitionEvidence DTOs")
        object.__setattr__(self, "evidence", evidence)
        require_string(self.reason, "reason", allow_empty=True)
        if not isinstance(self.status, RecognitionCaseStatus):
            try:
                object.__setattr__(self, "status", RecognitionCaseStatus(str(self.status)))
            except ValueError as error:
                raise ContractValidationError("unknown recognition case status") from error

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "RecognitionCase":
        payload = require_mapping(payload, "recognition_case")
        roots = mapping_get(payload, "project_roots", ())
        evidence = mapping_get(payload, "evidence", ())
        return cls(
            signature=require_string(mapping_get(payload, "signature", ""), "signature"),
            recognition_key=require_string(mapping_get(payload, "recognition_key", ""), "recognition_key"),
            display_name=require_string(mapping_get(payload, "display_name", ""), "display_name"),
            project_roots=tuple(
                item
                if isinstance(item, SourcePath)
                else SourcePath.from_dict(require_mapping(item, "project_roots"))
                for item in roots
            ),
            cities=string_tuple(mapping_get(payload, "cities", ()), "cities"),
            service_types=string_tuple(mapping_get(payload, "service_types", ()), "service_types"),
            years=tuple(require_int(item, "years", minimum=1900) for item in mapping_get(payload, "years", ())),
            reason=require_string(mapping_get(payload, "reason", ""), "reason", allow_empty=True),
            suggested_customer_ids=tuple(require_int(item, "suggested_customer_ids", minimum=1) for item in mapping_get(payload, "suggested_customer_ids", ())),
            evidence=tuple(
                item
                if isinstance(item, RecognitionEvidence)
                else RecognitionEvidence.from_dict(require_mapping(item, "evidence"))
                for item in evidence
            ),
            status=require_string(mapping_get(payload, "status", "pending"), "status"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class RecognitionDecision(JsonDto):
    signature: str
    action: RecognitionDecisionAction
    customer_id: int | None = None
    decided_at: str = ""

    def __post_init__(self) -> None:
        require_string(self.signature, "signature")
        if not isinstance(self.action, RecognitionDecisionAction):
            try:
                object.__setattr__(self, "action", RecognitionDecisionAction(str(self.action)))
            except ValueError as error:
                raise ContractValidationError("unknown recognition decision") from error
        optional_int(self.customer_id, "customer_id", minimum=1)
        require_string(self.decided_at, "decided_at", allow_empty=True)
        if self.action is RecognitionDecisionAction.ASSIGN and self.customer_id is None:
            raise ContractValidationError("assign requires customer_id")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "RecognitionDecision":
        payload = require_mapping(payload, "recognition_decision")
        return cls(
            signature=require_string(mapping_get(payload, "signature", ""), "signature"),
            action=require_string(mapping_get(payload, "action", ""), "action"),  # type: ignore[arg-type]
            customer_id=optional_int(mapping_get(payload, "customer_id", None), "customer_id", minimum=1),
            decided_at=require_string(mapping_get(payload, "decided_at", ""), "decided_at", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class RecognitionRunSummary(JsonDto):
    id: int | None = None
    detected: int = 0
    created: int = 0
    assigned: int = 0
    pending: int = 0
    rejected: int = 0
    error: str = ""
    started_at: str = ""
    finished_at: str = ""

    def __post_init__(self) -> None:
        optional_int(self.id, "id", minimum=1)
        for name in ("detected", "created", "assigned", "pending", "rejected"):
            require_int(getattr(self, name), name, minimum=0)
        for name in ("error", "started_at", "finished_at"):
            require_string(getattr(self, name), name, allow_empty=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "RecognitionRunSummary":
        payload = require_mapping(payload, "recognition_run")
        return cls(
            id=optional_int(mapping_get(payload, "id", None), "id", minimum=1),
            detected=require_int(mapping_get(payload, "detected", 0), "detected", minimum=0),
            created=require_int(mapping_get(payload, "created", 0), "created", minimum=0),
            assigned=require_int(mapping_get(payload, "assigned", 0), "assigned", minimum=0),
            pending=require_int(mapping_get(payload, "pending", 0), "pending", minimum=0),
            rejected=require_int(mapping_get(payload, "rejected", 0), "rejected", minimum=0),
            error=require_string(mapping_get(payload, "error", ""), "error", allow_empty=True),
            started_at=require_string(mapping_get(payload, "started_at", ""), "started_at", allow_empty=True),
            finished_at=require_string(mapping_get(payload, "finished_at", ""), "finished_at", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class CustomerSuggestion(JsonDto):
    """A reviewable document-derived value; never an implicit master-data write."""

    id: int
    customer_id: int
    field_name: str
    value: str
    source: SourcePath
    fingerprint: str
    excerpt: str = ""
    rule: str = ""
    confidence: float = 0.0
    status: CustomerSuggestionStatus = CustomerSuggestionStatus.PENDING
    suggestion_type: str = "field"
    contact: Contact | None = None
    created_at: str = ""
    resolved_at: str = ""

    def __post_init__(self) -> None:
        require_int(self.id, "id", minimum=1)
        require_int(self.customer_id, "customer_id", minimum=1)
        for name in ("field_name", "value", "fingerprint"):
            require_string(getattr(self, name), name)
        if not isinstance(self.source, SourcePath):
            raise ContractValidationError("source must be a SourcePath DTO")
        for name in ("excerpt", "rule", "created_at", "resolved_at"):
            require_string(getattr(self, name), name, allow_empty=True)
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise ContractValidationError("confidence must be between 0 and 1")
        if not isinstance(self.status, CustomerSuggestionStatus):
            try:
                object.__setattr__(self, "status", CustomerSuggestionStatus(str(self.status)))
            except ValueError as error:
                raise ContractValidationError("unknown customer suggestion status") from error
        if self.suggestion_type not in {"field", "contact"}:
            raise ContractValidationError("unknown customer suggestion type")
        if self.contact is not None and not isinstance(self.contact, Contact):
            raise ContractValidationError("contact must be a Contact DTO")
        if self.suggestion_type == "contact" and self.contact is None:
            raise ContractValidationError("contact suggestion requires contact data")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CustomerSuggestion":
        payload = require_mapping(payload, "customer_suggestion")
        confidence = mapping_get(payload, "confidence", 0.0)
        raw_contact = mapping_get(payload, "contact", None)
        suggestion_type = require_string(
            mapping_get(payload, "suggestion_type", "field"), "suggestion_type"
        )
        return cls(
            id=require_int(mapping_get(payload, "id", 0), "id", minimum=1),
            customer_id=require_int(
                mapping_get(payload, "customer_id", 0), "customer_id", minimum=1
            ),
            field_name=require_string(
                mapping_get(payload, "field_name", mapping_get(payload, "kind", "")),
                "field_name",
            ),
            value=require_string(mapping_get(payload, "value", ""), "value"),
            source=SourcePath.from_dict(
                require_mapping(mapping_get(payload, "source", payload), "source")
            ),
            fingerprint=require_string(
                mapping_get(payload, "fingerprint", ""), "fingerprint"
            ),
            excerpt=require_string(
                mapping_get(payload, "excerpt", ""), "excerpt", allow_empty=True
            ),
            rule=require_string(
                mapping_get(payload, "rule", ""), "rule", allow_empty=True
            ),
            confidence=(
                float(confidence)
                if isinstance(confidence, (int, float))
                and not isinstance(confidence, bool)
                else confidence
            ),
            status=require_string(mapping_get(payload, "status", "pending"), "status"),  # type: ignore[arg-type]
            suggestion_type=suggestion_type,
            contact=(
                Contact.from_dict(require_mapping(raw_contact, "contact"))
                if raw_contact is not None
                else None
            ),
            created_at=require_string(
                mapping_get(payload, "created_at", ""), "created_at", allow_empty=True
            ),
            resolved_at=require_string(
                mapping_get(payload, "resolved_at", ""),
                "resolved_at",
                allow_empty=True,
            ),
        )


Evidence = RecognitionEvidence
Decision = RecognitionDecision
RunSummary = RecognitionRunSummary
