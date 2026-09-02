"""Ports consumed by the client application layer.

Protocols live with their consumer.  Adapters can therefore be replaced in
tests without pulling HTTP, SQLite, filesystem or Qt concerns into use cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol

from papagui_contracts.catalog import CatalogFacets, CatalogFolder, CatalogProjectRoot
from papagui_contracts.customers import Customer, CustomerJournalEntry
from papagui_contracts.generations import (
    GenerationComponentKind,
    GenerationComponentManifest,
    GenerationManifest,
)

from .models import (
    CatalogQuery,
    CatalogRecord,
    GlobalSearchPage,
    GlobalSearchQuery,
    GatewayMutationResult,
    InstalledGeneration,
    PendingCustomerMutation,
    JournalGatewayResult,
    PendingJournalMutation,
)


class CatalogReader(Protocol):
    def search(self, query: CatalogQuery) -> list[CatalogRecord]: ...

    def get(self, document_key: str) -> CatalogRecord | None: ...

    def facets(self, source_id: str | None = None) -> CatalogFacets: ...

    def folders(
        self,
        source_id: str | None = None,
        *,
        query: str = "",
        project_only: bool = False,
    ) -> list[CatalogFolder]: ...

    def folder(
        self, source_id: str, relative_path: str
    ) -> tuple[CatalogFolder, list[CatalogFolder], list[CatalogRecord]]: ...

    def project_roots(self, source_id: str | None = None) -> list[CatalogProjectRoot]: ...

    def global_search(self, query: GlobalSearchQuery) -> GlobalSearchPage: ...


class SearchHistoryStore(Protocol):
    def entries(self) -> tuple[str, ...]: ...

    def add(self, query: str) -> tuple[str, ...]: ...

    def clear(self) -> None: ...


class GenerationGateway(Protocol):
    def current_manifest(self) -> GenerationManifest: ...

    def download_component(
        self,
        kind: GenerationComponentKind,
        component: GenerationComponentManifest,
        destination: Path,
    ) -> None: ...


class GenerationStore(Protocol):
    @property
    def root(self) -> Path: ...

    def import_legacy_symlink(self) -> bool: ...

    def current_generation(self, kind: GenerationComponentKind) -> str | None: ...

    def active_component_path(self, kind: GenerationComponentKind) -> Path | None: ...

    def install_archive(
        self,
        kind: GenerationComponentKind,
        component: GenerationComponentManifest,
        archive: Path,
        *,
        legacy_combined: bool = False,
    ) -> InstalledGeneration: ...

    def activate(self, installed: Iterable[InstalledGeneration]) -> Mapping[str, str]: ...

    def discard(self, installed: Iterable[InstalledGeneration]) -> None: ...


class CustomerSnapshot(Protocol):
    def list_customers(self) -> list[Customer]: ...

    def get_customer(self, customer_id: int) -> Customer | None: ...


class CustomerGateway(Protocol):
    def list_customers(self) -> list[Customer]: ...

    def get_customer(self, customer_id: int) -> Customer | None: ...

    def mutate(self, mutation: PendingCustomerMutation) -> GatewayMutationResult: ...


class CustomerOutbox(Protocol):
    def enqueue_upsert(
        self,
        customer: Customer,
        operation: str,
        expected_revision: int,
        aggregate_key: str | None = None,
    ) -> PendingCustomerMutation: ...

    def enqueue_delete(
        self, customer_id: int, expected_revision: int
    ) -> PendingCustomerMutation: ...

    def pending(self) -> list[PendingCustomerMutation]: ...

    def overlay(self) -> Mapping[str, tuple[str, Customer | None, bool]]: ...

    def overlay_states(self) -> Mapping[str, str]: ...

    def conflicts(self) -> list[PendingCustomerMutation]: ...

    def mark_applied(
        self, mutation: PendingCustomerMutation, result: GatewayMutationResult
    ) -> None: ...

    def mark_conflict(
        self, mutation: PendingCustomerMutation, current: Customer | None
    ) -> None: ...

    def reconcile(self, snapshot: CustomerSnapshot) -> None: ...

    def count_pending(self) -> int: ...

    def discard_local(self, aggregate_key: str) -> None: ...

    def discard_change(
        self, aggregate_key: str, current: Customer | None = None
    ) -> None: ...


CustomerSnapshotFactory = Callable[[], CustomerSnapshot]


class JournalGateway(Protocol):
    def list_entries(self, customer_id: int) -> tuple[list[CustomerJournalEntry], int]: ...

    def mutate(self, mutation: PendingJournalMutation) -> JournalGatewayResult: ...


class JournalOutbox(Protocol):
    def enqueue_upsert(
        self,
        customer_id: int,
        entry: CustomerJournalEntry,
        operation: str,
        expected_revision: int,
        aggregate_key: str | None = None,
    ) -> PendingJournalMutation: ...

    def enqueue_delete(
        self, customer_id: int, entry_id: int, expected_revision: int
    ) -> PendingJournalMutation: ...

    def pending(self) -> list[PendingJournalMutation]: ...

    def overlay(self) -> Mapping[str, tuple[str, CustomerJournalEntry | None, bool]]: ...

    def overlay_for_customer(
        self, customer_id: int
    ) -> Mapping[str, tuple[str, CustomerJournalEntry | None, bool]]: ...

    def overlay_states(self) -> Mapping[str, str]: ...

    def mark_applied(
        self, mutation: PendingJournalMutation, result: JournalGatewayResult
    ) -> None: ...

    def mark_conflict(
        self, mutation: PendingJournalMutation, current: Customer | None
    ) -> None: ...

    def count_pending(self) -> int: ...

    def reconcile(self, customer: Customer | None) -> None: ...

    def discard(self, aggregate_key: str) -> None: ...
