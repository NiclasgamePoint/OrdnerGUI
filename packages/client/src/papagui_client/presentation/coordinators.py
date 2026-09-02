"""Presentation orchestration without Qt widget or transport dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import RLock
from typing import Mapping, Protocol

from papagui_contracts import Customer, CustomerJournalEntry

from papagui_client.application.catalog import CatalogSearchService
from papagui_client.application.customers import OfflineFirstCustomerStore
from papagui_client.application.journals import OfflineFirstJournalStore
from papagui_client.application.models import (
    CatalogHit,
    CatalogQuery,
    CustomerView,
    CustomerWriteResult,
    JournalEntryView,
    JournalReplayResult,
    JournalWriteResult,
    PendingJournalMutation,
    PendingCustomerMutation,
    ReplayResult,
    SyncResult,
)
from papagui_client.application.sync import SyncCoordinator as GenerationSyncCoordinator


class ClientPage(StrEnum):
    SEARCH = "search"
    CUSTOMERS = "customers"
    CONNECTION = "connection"


class TimerPort(Protocol):
    def start(self, interval_seconds: int, callback: Callable[[], None]) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DataSession:
    catalog_search: CatalogSearchService
    customers: OfflineFirstCustomerStore
    generations: Mapping[str, str]
    journals: OfflineFirstJournalStore | None = None


@dataclass(frozen=True, slots=True)
class ConflictCase:
    aggregate_key: str
    mutation: PendingCustomerMutation
    local: Customer | None
    current: Customer | None


@dataclass(frozen=True, slots=True)
class JournalConflictCase:
    aggregate_key: str
    mutation: PendingJournalMutation
    local: CustomerJournalEntry | None
    current_customer: Customer | None


class DataSessionCoordinator:
    """Replace all snapshot readers as one unit after pointer activation."""

    def __init__(
        self,
        factory: Callable[[Mapping[str, str]], DataSession],
        generation_ids: Callable[[], Mapping[str, str]],
    ) -> None:
        self._factory = factory
        self._generation_ids = generation_ids
        self._lock = RLock()
        generations = dict(generation_ids())
        self._session = factory(generations)
        self._listeners: list[Callable[[DataSession], None]] = []

    @property
    def current(self) -> DataSession:
        with self._lock:
            return self._session

    def rebind(self, *, force: bool = False) -> bool:
        generations = dict(self._generation_ids())
        with self._lock:
            if not force and generations == dict(self._session.generations):
                return False
            replacement = self._factory(generations)
            self._session = replacement
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(replacement)
        return True

    def subscribe(self, listener: Callable[[DataSession], None]) -> None:
        with self._lock:
            self._listeners.append(listener)


class SearchCoordinator:
    def __init__(self, data_session: DataSessionCoordinator):
        self._data_session = data_session
        self.last_query = CatalogQuery()
        self.last_results: tuple[CatalogHit, ...] = ()

    def search(
        self, query: CatalogQuery | str = "", **filters: object
    ) -> list[CatalogHit]:
        if isinstance(query, CatalogQuery):
            if filters:
                raise TypeError("filters must be part of an explicit CatalogQuery")
            self.last_query = query
        else:
            self.last_query = CatalogQuery(text=query, **filters)
        self.last_results = tuple(
            self._data_session.current.catalog_search.search(self.last_query)
        )
        return list(self.last_results)

    def refresh(self) -> list[CatalogHit]:
        self.last_results = tuple(
            self._data_session.current.catalog_search.search(self.last_query)
        )
        return list(self.last_results)


class NavigationCoordinator:
    def __init__(self, initial: ClientPage = ClientPage.SEARCH):
        self._current = initial
        self._listeners: list[Callable[[ClientPage], None]] = []

    @property
    def current(self) -> ClientPage:
        return self._current

    def navigate(self, page: ClientPage | str) -> None:
        target = ClientPage(page)
        if target is self._current:
            return
        self._current = target
        for listener in tuple(self._listeners):
            listener(target)

    def subscribe(self, listener: Callable[[ClientPage], None]) -> None:
        self._listeners.append(listener)


class CustomerCoordinator:
    def __init__(self, data_session: DataSessionCoordinator):
        self._data_session = data_session
        self._conflicts: dict[str, ConflictCase] = {}

    def list(self) -> tuple[CustomerView, ...]:
        return tuple(self._store.list_customer_views())

    def save(
        self,
        customer: Customer,
        *,
        expected_revision: int | None = None,
        local_key: str | None = None,
    ) -> CustomerWriteResult:
        result = self._store.save(
            customer, expected_revision=expected_revision, local_key=local_key
        )
        self._capture(result.replay)
        return result

    def delete(self, customer: Customer, *, local_key: str | None = None) -> CustomerWriteResult:
        if customer.id is None:
            if not local_key:
                raise ValueError("local customer deletion requires its local key")
            self._store.discard_local(local_key)
            return CustomerWriteResult(customer=None, queued=False, replay=ReplayResult())
        result = self._store.delete(customer.id, customer.revision)
        self._capture(result.replay)
        return result

    def replay(self) -> ReplayResult:
        result = self._store.replay()
        self._capture(result)
        return result

    def conflicts(self) -> tuple[ConflictCase, ...]:
        pending = {item.idempotency_key: item for item in self._store.conflicts()}
        # Preserve current server payloads captured at the time of the 409.
        self._conflicts = {
            key: case for key, case in self._conflicts.items() if key in pending
        }
        for key, mutation in pending.items():
            if key not in self._conflicts:
                local = self._local_from_mutation(mutation)
                self._conflicts[key] = ConflictCase(
                    mutation.aggregate_key, mutation, local, None
                )
        return tuple(self._conflicts.values())

    def reload_from_server(self, case: ConflictCase) -> Customer | None:
        current = self._store.fetch_server_customer(case.current, case.aggregate_key)
        self._store.discard_change(case.aggregate_key, current)
        self._conflicts.pop(case.mutation.idempotency_key, None)
        return current

    def discard_local(self, case: ConflictCase) -> None:
        self._store.discard_change(case.aggregate_key)
        self._conflicts.pop(case.mutation.idempotency_key, None)

    def retry_against_current(self, case: ConflictCase) -> CustomerWriteResult:
        if case.mutation.operation.value == "delete":
            self._store.discard_change(case.aggregate_key)
            self._conflicts.pop(case.mutation.idempotency_key, None)
            if case.current is None or case.current.id is None:
                return CustomerWriteResult(None, False, ReplayResult())
            return self._store.delete(case.current.id, case.current.revision)
        if case.local is None or case.current is None:
            raise ValueError("retry requires both local and current customer values")
        self._store.discard_change(case.aggregate_key)
        self._conflicts.pop(case.mutation.idempotency_key, None)
        local = replace(case.local, id=case.current.id, revision=case.current.revision)
        return self.save(local, expected_revision=case.current.revision)

    def save_merge(self, case: ConflictCase, merged: Customer) -> CustomerWriteResult:
        if case.current is None or case.current.id is None:
            raise ValueError("merge requires the current server customer")
        self._store.discard_change(case.aggregate_key)
        self._conflicts.pop(case.mutation.idempotency_key, None)
        value = replace(merged, id=case.current.id, revision=case.current.revision)
        return self.save(value, expected_revision=case.current.revision)

    @property
    def _store(self) -> OfflineFirstCustomerStore:
        return self._data_session.current.customers

    def _capture(self, result: ReplayResult) -> None:
        mutations = {item.idempotency_key: item for item in self._store.conflicts()}
        for conflict in result.conflicts:
            mutation = mutations.get(conflict.idempotency_key)
            if mutation is not None:
                self._conflicts[conflict.idempotency_key] = ConflictCase(
                    mutation.aggregate_key,
                    mutation,
                    conflict.local,
                    conflict.current,
                )

    @staticmethod
    def _local_from_mutation(mutation: PendingCustomerMutation) -> Customer | None:
        if not mutation.payload:
            return None
        return Customer.from_dict(mutation.payload)


class JournalCoordinator:
    def __init__(self, data_session: DataSessionCoordinator):
        self._data_session = data_session
        self._conflicts: dict[str, JournalConflictCase] = {}

    def list(self, customer_id: int) -> tuple[JournalEntryView, ...]:
        return self._store.list_entries(customer_id)

    def save(
        self,
        customer_id: int,
        entry,
        expected_revision: int,
        *,
        local_key: str | None = None,
    ) -> JournalWriteResult:
        result = self._store.save(
            customer_id,
            entry,
            expected_revision,
            local_key=local_key,
        )
        self._capture(result.replay)
        return result

    def delete(
        self, customer_id: int, entry_id: int, expected_revision: int
    ) -> JournalWriteResult:
        result = self._store.delete(customer_id, entry_id, expected_revision)
        self._capture(result.replay)
        return result

    def replay(self) -> JournalReplayResult:
        result = self._store.replay()
        self._capture(result)
        return result

    def discard(self, aggregate_key: str) -> None:
        self._store.discard(aggregate_key)

    def conflicts(self) -> tuple[JournalConflictCase, ...]:
        pending = {item.idempotency_key: item for item in self._store.conflicts()}
        self._conflicts = {
            key: case for key, case in self._conflicts.items() if key in pending
        }
        for key, mutation in pending.items():
            if key not in self._conflicts:
                self._conflicts[key] = JournalConflictCase(
                    mutation.aggregate_key,
                    mutation,
                    self._entry_from_mutation(mutation),
                    None,
                )
        return tuple(self._conflicts.values())

    def discard_conflict(self, case: JournalConflictCase) -> None:
        self._store.discard(case.aggregate_key)
        self._conflicts.pop(case.mutation.idempotency_key, None)

    def retry_against_current(
        self,
        case: JournalConflictCase,
        entry: CustomerJournalEntry | None = None,
    ) -> JournalWriteResult:
        if case.current_customer is None:
            raise ValueError("retry requires the current customer revision")
        if case.mutation.operation.value == "delete":
            target_id = case.mutation.target_id
            if target_id is None:
                raise ValueError("journal delete retry requires an entry id")
            self.discard_conflict(case)
            return self.delete(
                case.mutation.customer_id,
                target_id,
                case.current_customer.revision,
            )
        value = entry or case.local
        if value is None:
            raise ValueError("retry requires a local journal entry")
        self.discard_conflict(case)
        return self.save(
            case.mutation.customer_id,
            value,
            case.current_customer.revision,
        )

    @property
    def _store(self) -> OfflineFirstJournalStore:
        store = self._data_session.current.journals
        if store is None:
            raise RuntimeError("journal store is not configured")
        return store

    def _capture(self, result: JournalReplayResult) -> None:
        mutations = {item.idempotency_key: item for item in self._store.conflicts()}
        for conflict in result.conflicts:
            mutation = mutations.get(conflict.idempotency_key)
            if mutation is not None:
                self._conflicts[conflict.idempotency_key] = JournalConflictCase(
                    mutation.aggregate_key,
                    mutation,
                    conflict.local,
                    conflict.current_customer,
                )

    @staticmethod
    def _entry_from_mutation(
        mutation: PendingJournalMutation,
    ) -> CustomerJournalEntry | None:
        return (
            CustomerJournalEntry.from_dict(mutation.payload)
            if mutation.payload
            else None
        )


class SyncCoordinator:
    """Bind generation sync, data-session rollover and periodic scheduling."""

    def __init__(
        self,
        generations: GenerationSyncCoordinator,
        data_session: DataSessionCoordinator,
        interval_seconds: int,
    ) -> None:
        self._generations = generations
        self._data_session = data_session
        self.interval_seconds = interval_seconds
        self._timer: TimerPort | None = None
        self._request_sync: Callable[[], None] | None = None

    def sync(self) -> SyncResult:
        result = self._generations.sync()
        if result.changed:
            self._data_session.rebind(force=True)
        replay = self._data_session.current.customers.replay()
        journals = self._data_session.current.journals
        journal_replay = journals.replay() if journals is not None else None
        return replace(
            result,
            customer_replay=replay,
            journal_replay=journal_replay,
        )

    def has_local_data(self) -> bool:
        return self._generations.has_local_data()

    def start(
        self,
        timer: TimerPort,
        request_sync: Callable[[], None],
        *,
        immediate: bool = True,
    ) -> None:
        self.stop()
        self._timer = timer
        self._request_sync = request_sync
        timer.start(self.interval_seconds, request_sync)
        if immediate:
            request_sync()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def reconfigure(
        self,
        generations: GenerationSyncCoordinator,
        interval_seconds: int,
    ) -> None:
        """Swap remote wiring and reschedule an active timer without eager I/O."""
        if not 900 <= interval_seconds <= 172_800:
            raise ValueError("sync interval must be between 15 minutes and 48 hours")
        timer = self._timer
        callback = self._request_sync
        if timer is not None:
            timer.stop()
        self._generations = generations
        self.interval_seconds = interval_seconds
        if timer is not None and callback is not None:
            self._timer = timer
            timer.start(interval_seconds, callback)
