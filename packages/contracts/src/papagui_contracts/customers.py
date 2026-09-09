"""Immutable customer, contact, journal, and mutation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping
import uuid

from ._base import (
    ContractValidationError,
    JsonDto,
    JsonValue,
    mapping_get,
    optional_int,
    require_int,
    require_mapping,
    require_string,
    string_tuple,
)
from .generations import SourcePath


_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def _tuple_payload(value: object, field_name: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be an array")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class Contact(JsonDto):
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("name", "role", "email", "phone"):
            require_string(getattr(self, field_name), field_name, allow_empty=True)
        if self.id is not None:
            require_string(self.id, "contact.id")
            if len(self.id) > 128:
                raise ContractValidationError("contact.id is too long")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "Contact":
        payload = require_mapping(payload, "contact")
        return cls(
            name=require_string(mapping_get(payload, "name", ""), "name", allow_empty=True),
            role=require_string(mapping_get(payload, "role", ""), "role", allow_empty=True),
            email=require_string(mapping_get(payload, "email", ""), "email", allow_empty=True),
            phone=require_string(mapping_get(payload, "phone", ""), "phone", allow_empty=True),
            id=mapping_get(payload, "id", None),
        )


@dataclass(frozen=True, slots=True)
class CustomerJournalEntry(JsonDto):
    id: int | None = None
    customer_id: int | None = None
    entry_number: int = 0
    title: str = ""
    body: str = ""
    created_at: str = ""
    updated_at: str = ""
    revision: int = 0

    def __post_init__(self) -> None:
        optional_int(self.id, "id", minimum=1)
        optional_int(self.customer_id, "customer_id", minimum=1)
        require_int(self.entry_number, "entry_number", minimum=0)
        require_int(self.revision, "revision", minimum=0)
        for field_name in ("title", "body", "created_at", "updated_at"):
            require_string(getattr(self, field_name), field_name, allow_empty=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CustomerJournalEntry":
        payload = require_mapping(payload, "journal_entry")
        return cls(
            id=optional_int(mapping_get(payload, "id", None), "id", minimum=1),
            customer_id=optional_int(
                mapping_get(payload, "customer_id", None),
                "customer_id",
                minimum=1,
            ),
            entry_number=require_int(
                mapping_get(payload, "entry_number", 0),
                "entry_number",
                minimum=0,
            ),
            title=require_string(mapping_get(payload, "title", ""), "title", allow_empty=True),
            body=require_string(mapping_get(payload, "body", ""), "body", allow_empty=True),
            created_at=require_string(
                mapping_get(payload, "created_at", ""),
                "created_at",
                allow_empty=True,
            ),
            updated_at=require_string(
                mapping_get(payload, "updated_at", ""),
                "updated_at",
                allow_empty=True,
            ),
            revision=require_int(mapping_get(payload, "revision", 0), "revision", minimum=0),
        )


JournalEntry = CustomerJournalEntry


@dataclass(frozen=True, slots=True)
class CustomerProject(JsonDto):
    id: int | None = None
    customer_id: int | None = None
    project_root_id: int | None = None
    source: SourcePath | None = None
    service_type: str = ""
    project_label: str = ""
    project_city: str = ""
    year: int | None = None
    provenance: str = "folder"

    def __post_init__(self) -> None:
        optional_int(self.id, "id", minimum=1)
        optional_int(self.customer_id, "customer_id", minimum=1)
        optional_int(self.project_root_id, "project_root_id", minimum=1)
        if self.source is not None and not isinstance(self.source, SourcePath):
            raise ContractValidationError("source must be a SourcePath DTO")
        optional_int(self.year, "year", minimum=1900)
        for name in ("service_type", "project_label", "project_city", "provenance"):
            require_string(getattr(self, name), name, allow_empty=name != "provenance")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CustomerProject":
        payload = require_mapping(payload, "customer_project")
        raw_source = mapping_get(payload, "source", None)
        if raw_source is None and mapping_get(payload, "source_id", None) is not None:
            raw_source = payload
        return cls(
            id=optional_int(mapping_get(payload, "id", None), "id", minimum=1),
            customer_id=optional_int(mapping_get(payload, "customer_id", None), "customer_id", minimum=1),
            project_root_id=optional_int(mapping_get(payload, "project_root_id", None), "project_root_id", minimum=1),
            source=(SourcePath.from_dict(require_mapping(raw_source, "source")) if raw_source is not None else None),
            service_type=require_string(mapping_get(payload, "service_type", ""), "service_type", allow_empty=True),
            project_label=require_string(mapping_get(payload, "project_label", ""), "project_label", allow_empty=True),
            project_city=require_string(mapping_get(payload, "project_city", mapping_get(payload, "city", "")), "project_city", allow_empty=True),
            year=optional_int(mapping_get(payload, "year", None), "year", minimum=1900),
            provenance=require_string(mapping_get(payload, "provenance", mapping_get(payload, "source_type", "folder")), "provenance"),
        )


@dataclass(frozen=True, slots=True)
class Customer(JsonDto):
    id: int | None = None
    revision: int = 0
    folder_path: str = ""
    folder_paths: tuple[str, ...] = ()
    display_name: str = ""
    entity_type: str = "Unternehmen"
    service_types: tuple[str, ...] = ()
    company: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    contacts: tuple[Contact, ...] = ()
    journal_entries: tuple[CustomerJournalEntry, ...] = ()
    notes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    projects: tuple[CustomerProject, ...] = ()

    def __post_init__(self) -> None:
        optional_int(self.id, "id", minimum=1)
        require_int(self.revision, "revision", minimum=0)
        for field_name in (
            "folder_path",
            "display_name",
            "entity_type",
            "company",
            "email",
            "phone",
            "street",
            "postal_code",
            "city",
        ):
            require_string(getattr(self, field_name), field_name, allow_empty=True)
        for field_name in ("folder_paths", "service_types", "notes", "tags"):
            raw = getattr(self, field_name)
            normalized = string_tuple(raw, field_name)
            object.__setattr__(self, field_name, normalized)
        contacts = tuple(self.contacts)
        if not all(isinstance(contact, Contact) for contact in contacts):
            raise ContractValidationError("contacts must contain Contact DTOs")
        object.__setattr__(self, "contacts", contacts)
        journal_entries = tuple(self.journal_entries)
        if not all(isinstance(entry, CustomerJournalEntry) for entry in journal_entries):
            raise ContractValidationError("journal_entries must contain CustomerJournalEntry DTOs")
        object.__setattr__(self, "journal_entries", journal_entries)
        projects = tuple(self.projects)
        if not all(isinstance(project, CustomerProject) for project in projects):
            raise ContractValidationError("projects must contain CustomerProject DTOs")
        object.__setattr__(self, "projects", projects)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "Customer":
        payload = require_mapping(payload, "customer")
        raw_contacts = _tuple_payload(mapping_get(payload, "contacts", ()), "contacts")
        contacts = tuple(
            item
            if isinstance(item, Contact)
            else Contact.from_dict(require_mapping(item, "contacts"))
            for item in raw_contacts
        )
        raw_journal = _tuple_payload(
            mapping_get(
                payload,
                "journal_entries",
                mapping_get(payload, "journal", ()),
            ),
            "journal_entries",
        )
        journal_entries = tuple(
            item
            if isinstance(item, CustomerJournalEntry)
            else CustomerJournalEntry.from_dict(require_mapping(item, "journal_entries"))
            for item in raw_journal
        )
        raw_projects = _tuple_payload(mapping_get(payload, "projects", ()), "projects")
        projects = tuple(
            item
            if isinstance(item, CustomerProject)
            else CustomerProject.from_dict(require_mapping(item, "projects"))
            for item in raw_projects
        )
        return cls(
            id=optional_int(mapping_get(payload, "id", None), "id", minimum=1),
            revision=require_int(mapping_get(payload, "revision", 0), "revision", minimum=0),
            folder_path=require_string(
                mapping_get(payload, "folder_path", ""),
                "folder_path",
                allow_empty=True,
            ),
            folder_paths=string_tuple(mapping_get(payload, "folder_paths", ()), "folder_paths"),
            display_name=require_string(
                mapping_get(payload, "display_name", ""),
                "display_name",
                allow_empty=True,
            ),
            entity_type=require_string(
                mapping_get(payload, "entity_type", "Unternehmen"),
                "entity_type",
                allow_empty=True,
            ),
            service_types=string_tuple(mapping_get(payload, "service_types", ()), "service_types"),
            company=require_string(
                mapping_get(payload, "company", ""), "company", allow_empty=True
            ),
            email=require_string(mapping_get(payload, "email", ""), "email", allow_empty=True),
            phone=require_string(mapping_get(payload, "phone", ""), "phone", allow_empty=True),
            street=require_string(mapping_get(payload, "street", ""), "street", allow_empty=True),
            postal_code=require_string(
                mapping_get(payload, "postal_code", ""),
                "postal_code",
                allow_empty=True,
            ),
            city=require_string(mapping_get(payload, "city", ""), "city", allow_empty=True),
            contacts=contacts,
            journal_entries=journal_entries,
            notes=string_tuple(mapping_get(payload, "notes", ()), "notes"),
            tags=string_tuple(mapping_get(payload, "tags", ()), "tags"),
            projects=projects,
        )


class MutationOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class MutationTarget(str, Enum):
    CUSTOMER = "customer"
    JOURNAL = "journal"


@dataclass(frozen=True, slots=True)
class IdempotencyKey(JsonDto):
    value: str

    def __post_init__(self) -> None:
        value = require_string(self.value, "idempotency_key")
        if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
            raise ContractValidationError(
                "idempotency_key must contain 8-128 safe ASCII characters"
            )

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, JsonValue]:
        return {"value": self.value}

    @classmethod
    def new(cls) -> "IdempotencyKey":
        return cls(str(uuid.uuid4()))

    @classmethod
    def from_value(cls, payload: object) -> "IdempotencyKey":
        if isinstance(payload, cls):
            return payload
        if isinstance(payload, Mapping):
            payload = mapping_get(payload, "value", "")
        return cls(require_string(payload, "idempotency_key"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "IdempotencyKey":
        return cls.from_value(payload)


MutationPayload = Customer | CustomerJournalEntry | None


@dataclass(frozen=True, slots=True)
class MutationEnvelope(JsonDto):
    """A replay-safe optimistic customer or journal mutation."""

    operation: MutationOperation
    target: MutationTarget
    idempotency_key: IdempotencyKey
    expected_revision: int | None = None
    target_id: int | None = None
    customer_id: int | None = None
    payload: MutationPayload = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, MutationOperation):
            try:
                object.__setattr__(self, "operation", MutationOperation(str(self.operation)))
            except ValueError as exc:
                raise ContractValidationError("unknown mutation operation") from exc
        if not isinstance(self.target, MutationTarget):
            try:
                object.__setattr__(self, "target", MutationTarget(str(self.target)))
            except ValueError as exc:
                raise ContractValidationError("unknown mutation target") from exc
        if not isinstance(self.idempotency_key, IdempotencyKey):
            object.__setattr__(
                self,
                "idempotency_key",
                IdempotencyKey.from_value(self.idempotency_key),
            )
        optional_int(self.expected_revision, "expected_revision", minimum=0)
        optional_int(self.target_id, "target_id", minimum=1)
        optional_int(self.customer_id, "customer_id", minimum=1)
        if self.operation in {MutationOperation.UPDATE, MutationOperation.DELETE}:
            if self.expected_revision is None:
                raise ContractValidationError("expected_revision is required for update and delete")
            if self.target_id is None:
                raise ContractValidationError("target_id is required for update and delete")
        if self.target is MutationTarget.JOURNAL and self.customer_id is None:
            raise ContractValidationError("customer_id is required for journal mutations")
        if self.operation is MutationOperation.DELETE:
            if self.payload is not None:
                raise ContractValidationError("delete mutations must not contain payload")
            return
        expected_payload_type = (
            Customer if self.target is MutationTarget.CUSTOMER else CustomerJournalEntry
        )
        if not isinstance(self.payload, expected_payload_type):
            raise ContractValidationError(
                f"{self.target.value} {self.operation.value} requires a matching payload"
            )
        if (
            self.target is MutationTarget.CUSTOMER
            and self.target_id is not None
            and self.payload.id is not None
            and self.target_id != self.payload.id
        ):
            raise ContractValidationError("target_id and customer payload id differ")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operation": self.operation.value,
            "target": self.target.value,
            "idempotency_key": self.idempotency_key.value,
            "expected_revision": self.expected_revision,
            "target_id": self.target_id,
            "customer_id": self.customer_id,
            "payload": self.payload.to_dict() if self.payload is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "MutationEnvelope":
        payload = require_mapping(payload, "mutation")
        try:
            operation = MutationOperation(
                require_string(mapping_get(payload, "operation", ""), "operation")
            )
        except ValueError as exc:
            raise ContractValidationError("unknown mutation operation") from exc
        try:
            target = MutationTarget(
                require_string(
                    mapping_get(payload, "target", MutationTarget.CUSTOMER.value),
                    "target",
                )
            )
        except ValueError as exc:
            raise ContractValidationError("unknown mutation target") from exc
        raw_payload = mapping_get(
            payload,
            "payload",
            mapping_get(
                payload,
                "customer" if target is MutationTarget.CUSTOMER else "journal_entry",
                None,
            ),
        )
        dto_payload: MutationPayload = None
        if raw_payload is not None:
            payload_mapping = require_mapping(raw_payload, "mutation.payload")
            dto_payload = (
                Customer.from_dict(payload_mapping)
                if target is MutationTarget.CUSTOMER
                else CustomerJournalEntry.from_dict(payload_mapping)
            )
        return cls(
            operation=operation,
            target=target,
            idempotency_key=IdempotencyKey.from_value(mapping_get(payload, "idempotency_key", "")),
            expected_revision=optional_int(
                mapping_get(payload, "expected_revision", None),
                "expected_revision",
                minimum=0,
            ),
            target_id=optional_int(mapping_get(payload, "target_id", None), "target_id", minimum=1),
            customer_id=optional_int(
                mapping_get(payload, "customer_id", None),
                "customer_id",
                minimum=1,
            ),
            payload=dto_payload,
        )


CustomerMutationEnvelope = MutationEnvelope
