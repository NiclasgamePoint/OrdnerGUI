"""Client-owned values crossing application/adapter boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath
from typing import Any, Mapping

from papagui_contracts.catalog import CatalogFacets, CatalogFolder, CatalogProjectRoot
from papagui_contracts.customers import Customer, CustomerJournalEntry


class CustomerMutationKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class PendingMutationState(StrEnum):
    PENDING = "pending"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    text: str = ""
    source_id: str | None = None
    file_type: str | None = None
    customer_name: str | None = None
    year: str | None = None
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        if self.offset < 0:
            raise ValueError("offset must not be negative")


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    document_key: str
    source_id: str
    relative_path: str
    filename: str
    file_type: str = ""
    customer_name: str = ""
    project_name: str = ""
    year: str = ""
    modified_date: str = ""
    file_size: int = 0
    domain_folder: str = ""
    time_bucket: str = ""
    relative_dir: str = ""
    folder_id: int | None = None
    project_root_id: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CatalogHit:
    record: CatalogRecord
    local_path: PurePath


class GlobalSearchKind(StrEnum):
    DOCUMENT = "document"
    FOLDER = "folder"
    PROJECT = "project"
    CUSTOMER = "customer"


class GlobalSearchSort(StrEnum):
    RELEVANCE = "relevance"
    MODIFIED = "modified"
    NAME = "name"


@dataclass(frozen=True, slots=True)
class GlobalSearchQuery:
    text: str = ""
    kinds: tuple[GlobalSearchKind, ...] = ()
    source_id: str | None = None
    domain_folder: str | None = None
    year: str | None = None
    file_type: str | None = None
    customer_name: str | None = None
    sort: GlobalSearchSort = GlobalSearchSort.RELEVANCE
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        kinds = tuple(GlobalSearchKind(value) for value in self.kinds)
        object.__setattr__(self, "kinds", kinds)
        if not isinstance(self.sort, GlobalSearchSort):
            object.__setattr__(self, "sort", GlobalSearchSort(self.sort))
        if not 1 <= self.limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        if self.offset < 0:
            raise ValueError("offset must not be negative")

    @property
    def selected_kinds(self) -> tuple[GlobalSearchKind, ...]:
        return self.kinds or tuple(GlobalSearchKind)


@dataclass(frozen=True, slots=True)
class GlobalSearchRecord:
    kind: GlobalSearchKind
    key: str
    title: str
    subtitle: str = ""
    source_id: str = ""
    relative_path: str = ""
    modified_date: str = ""
    customer_id: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GlobalSearchHit:
    record: GlobalSearchRecord
    local_path: PurePath | None = None


@dataclass(frozen=True, slots=True)
class GlobalSearchPage:
    items: tuple[GlobalSearchHit, ...]
    total: int
    limit: int
    offset: int
    facets: CatalogFacets = field(default_factory=CatalogFacets)
    kind_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def has_previous(self) -> bool:
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(frozen=True, slots=True)
class CatalogFolderHit:
    folder: CatalogFolder
    local_path: PurePath


@dataclass(frozen=True, slots=True)
class CatalogProjectRootHit:
    project: CatalogProjectRoot
    local_path: PurePath


@dataclass(frozen=True, slots=True)
class CatalogFolderDetails:
    folder: CatalogFolderHit
    children: tuple[CatalogFolderHit, ...] = ()
    documents: tuple[CatalogHit, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingCustomerMutation:
    sequence: int
    idempotency_key: str
    aggregate_key: str
    operation: CustomerMutationKind
    expected_revision: int
    payload: Mapping[str, Any]
    state: PendingMutationState = PendingMutationState.PENDING
    conflict: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class GatewayMutationResult:
    customer: Customer | None = None
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class CustomerConflict:
    idempotency_key: str
    local: Customer | None
    current: Customer | None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    applied: int = 0
    applied_customers: Mapping[str, Customer] = field(default_factory=dict)
    conflicts: tuple[CustomerConflict, ...] = ()
    unavailable: bool = False
    remaining: int = 0


@dataclass(frozen=True, slots=True)
class CustomerWriteResult:
    customer: Customer | None
    queued: bool
    replay: ReplayResult
    local_key: str | None = None


class CustomerSyncState(StrEnum):
    SYNCED = "synced"
    PENDING = "pending"
    CONFLICT = "conflict"
    AWAITING_SNAPSHOT = "awaiting_snapshot"


@dataclass(frozen=True, slots=True)
class CustomerView:
    key: str
    customer: Customer
    sync_state: CustomerSyncState = CustomerSyncState.SYNCED


class JournalMutationKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class PendingJournalMutation:
    sequence: int
    idempotency_key: str
    aggregate_key: str
    customer_id: int
    operation: JournalMutationKind
    expected_revision: int
    payload: Mapping[str, Any]
    target_id: int | None = None
    state: PendingMutationState = PendingMutationState.PENDING
    conflict: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class JournalGatewayResult:
    entry: CustomerJournalEntry | None
    revision: int
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class JournalConflict:
    idempotency_key: str
    local: CustomerJournalEntry | None
    current_customer: Customer | None


@dataclass(frozen=True, slots=True)
class JournalReplayResult:
    applied: int = 0
    applied_entries: Mapping[str, CustomerJournalEntry] = field(default_factory=dict)
    conflicts: tuple[JournalConflict, ...] = ()
    unavailable: bool = False
    remaining: int = 0


@dataclass(frozen=True, slots=True)
class JournalWriteResult:
    entry: CustomerJournalEntry | None
    queued: bool
    replay: JournalReplayResult
    local_key: str | None = None


@dataclass(frozen=True, slots=True)
class JournalEntryView:
    key: str
    entry: CustomerJournalEntry
    sync_state: CustomerSyncState = CustomerSyncState.SYNCED


@dataclass(frozen=True, slots=True)
class InstalledGeneration:
    component: str
    generation: str
    path: str
    created: bool


@dataclass(frozen=True, slots=True)
class SyncResult:
    changed_components: tuple[str, ...] = ()
    current: Mapping[str, str] = field(default_factory=dict)
    customer_replay: ReplayResult | None = None
    journal_replay: JournalReplayResult | None = None
    awaiting_generation: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.changed_components)
