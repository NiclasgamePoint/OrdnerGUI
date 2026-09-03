"""Explicit composition root for the production client adapters."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Mapping

from papagui_contracts.generations import GenerationComponentKind

from .adapters.filesystem_generations import FilesystemGenerationStore
from .adapters.http_api import (
    HttpCustomerGateway,
    HttpGenerationGateway,
    HttpServerControlGateway,
)
from .adapters.http_journal import HttpJournalGateway
from .adapters.http_review import HttpReviewGateway
from .adapters.json_config import JsonClientConfigRepository
from .adapters.json_search_history import JsonSearchHistoryRepository
from .adapters.sqlite_catalog import SQLiteCatalogReader
from .adapters.sqlite_customers import SQLiteCustomerOutbox, SQLiteCustomerSnapshot
from .adapters.sqlite_journal_outbox import SQLiteJournalOutbox
from .application.catalog import CatalogSearchService
from .application.customers import OfflineFirstCustomerStore
from .application.journals import OfflineFirstJournalStore
from .application.paths import SourcePathResolver
from .application.sync import SyncCoordinator as GenerationSyncCoordinator
from .config import ClientSettings
from .presentation.coordinators import (
    CustomerCoordinator,
    DataSession,
    DataSessionCoordinator,
    NavigationCoordinator,
    JournalCoordinator,
    SearchCoordinator,
    SyncCoordinator,
)


class ClientContainer:
    """Own long-lived client services without global mutable singletons."""

    def __init__(
        self,
        settings: ClientSettings,
        *,
        config_repository: JsonClientConfigRepository | None = None,
        config_sources: Mapping[str, str] | None = None,
        server_control: bool = False,
    ):
        self.settings = settings
        self.config_repository = config_repository or JsonClientConfigRepository(
            settings.data_root / "client-config.json", data_root=settings.data_root
        )
        self.config_sources = dict(config_sources or {})
        self._server_control_enabled = server_control
        self._migrate_storage_layout()
        self.generation_store = FilesystemGenerationStore(settings.cache_root)
        self.generation_gateway = HttpGenerationGateway(
            settings.server_url,
            token=settings.api_token,
            timeout_seconds=settings.timeout_seconds,
        )
        self.generation_sync = GenerationSyncCoordinator(
            self.generation_gateway, self.generation_store
        )
        self.paths = SourcePathResolver(settings.source_mappings)
        self.search_history = JsonSearchHistoryRepository(settings.search_history_path)
        self.customer_outbox = SQLiteCustomerOutbox(settings.outbox_path)
        self.journal_outbox = SQLiteJournalOutbox(settings.outbox_path)
        for legacy_path in settings.legacy_outbox_paths:
            self.customer_outbox.import_legacy_queue(legacy_path)
        self.customer_gateway = HttpCustomerGateway(
            settings.server_url,
            token=settings.api_token,
            timeout_seconds=settings.timeout_seconds,
        )
        self.journal_gateway = HttpJournalGateway(
            settings.server_url,
            token=settings.api_token,
            timeout_seconds=settings.timeout_seconds,
        )
        self.data_session = DataSessionCoordinator(
            self._build_data_session, self._generation_ids
        )
        self.search = SearchCoordinator(self.data_session)
        self.customers = CustomerCoordinator(self.data_session)
        self.journals = JournalCoordinator(self.data_session)
        self.navigation = NavigationCoordinator()
        self.sync = SyncCoordinator(
            self.generation_sync,
            self.data_session,
            settings.sync_interval_seconds,
        )
        self.review_gateway = HttpReviewGateway(
            settings.server_url, settings.api_token, settings.timeout_seconds
        )
        self.server_control = (
            HttpServerControlGateway(
                settings.server_url,
                token=settings.api_token,
                timeout_seconds=settings.timeout_seconds,
            )
            if server_control
            else None
        )

    @property
    def catalog_search(self) -> CatalogSearchService:
        return self.data_session.current.catalog_search

    @property
    def customer_store(self) -> OfflineFirstCustomerStore:
        return self.data_session.current.customers

    def _build_data_session(self, generations) -> DataSession:
        catalog_reader = SQLiteCatalogReader(
            self.catalog_database,
            self.customer_database,
        )
        customer_snapshot = SQLiteCustomerSnapshot(self.customer_database)
        customers = OfflineFirstCustomerStore(
            customer_snapshot,
            self.customer_outbox,
            self.customer_gateway,
        )
        journals = OfflineFirstJournalStore(
            customer_snapshot,
            self.journal_outbox,
            self.journal_gateway,
        )
        return DataSession(
            catalog_search=CatalogSearchService(
                catalog_reader,
                self.paths,
                self.search_history,
            ),
            customers=customers,
            journals=journals,
            generations=dict(generations),
        )

    def _generation_ids(self) -> dict[str, str]:
        result = {}
        for kind in (GenerationComponentKind.INDEX, GenerationComponentKind.CUSTOMERS):
            generation = self.generation_store.current_generation(kind)
            if generation is not None:
                result[kind.value] = generation
        return result

    def catalog_database(self) -> Path:
        root = self.generation_store.active_component_path(GenerationComponentKind.INDEX)
        return self._first_file(
            root,
            "index/catalog/active.db",
            "catalog/active.db",
            "index/catalog.db",
            "catalog.db",
        )

    def customer_database(self) -> Path:
        root = self.generation_store.active_component_path(GenerationComponentKind.CUSTOMERS)
        return self._first_file(root, "customers.db", "customer/customers.db")

    @property
    def onboarding_required(self) -> bool:
        return not self.settings.server_url.strip() or not self.settings.source_mappings

    def save_client_settings(self, settings: ClientSettings) -> ClientSettings:
        """Persist preferences, then re-apply environment overrides visibly."""
        if settings.data_root.resolve() != self.settings.data_root.resolve():
            raise ValueError("Ein Wechsel des Clientdatenordners erfordert einen Neustart")
        self.config_repository.save(settings)
        resolved = self.config_repository.resolve()
        self.config_sources = dict(resolved.sources)
        self.reconfigure(resolved.settings)
        return resolved.settings

    def reconfigure(self, settings: ClientSettings) -> None:
        """Rewire client-only adapters without ever touching server implementation."""
        if settings.data_root.resolve() != self.settings.data_root.resolve():
            raise ValueError("Ein Wechsel des Clientdatenordners erfordert einen Neustart")
        self.settings = settings
        self.generation_gateway = HttpGenerationGateway(
            settings.server_url,
            token=settings.api_token,
            timeout_seconds=settings.timeout_seconds,
        )
        self.generation_sync = GenerationSyncCoordinator(
            self.generation_gateway, self.generation_store
        )
        self.paths = SourcePathResolver(settings.source_mappings)
        self.customer_gateway = HttpCustomerGateway(
            settings.server_url,
            token=settings.api_token,
            timeout_seconds=settings.timeout_seconds,
        )
        self.journal_gateway = HttpJournalGateway(
            settings.server_url,
            token=settings.api_token,
            timeout_seconds=settings.timeout_seconds,
        )
        self.review_gateway = HttpReviewGateway(
            settings.server_url, settings.api_token, settings.timeout_seconds
        )
        self.server_control = (
            HttpServerControlGateway(
                settings.server_url,
                token=settings.api_token,
                timeout_seconds=settings.timeout_seconds,
            )
            if self._server_control_enabled
            else None
        )
        self.data_session.rebind(force=True)
        self.sync.reconfigure(self.generation_sync, settings.sync_interval_seconds)

    @staticmethod
    def _first_file(root: Path | None, *relative_paths: str) -> Path:
        if root is None:
            # Return a deterministic absent path; readers raise a focused error on use.
            return Path("__papagui_no_active_generation__")
        for relative in relative_paths:
            candidate = root / relative
            if candidate.is_file():
                return candidate
        return root / relative_paths[0]

    def _migrate_storage_layout(self) -> None:
        """Adopt the short-lived pre-0.4.2 cache layout without overwriting data."""
        root = self.settings.data_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)

        # An early 0.4.2 build used ``data/generations`` as the store root and
        # consequently produced ``generations/generations``. Move its payload
        # only when no canonical pointer exists; the pointer is moved last so a
        # crash can never activate a half-migrated layout.
        canonical_pointer = root / "active-generation.json"
        legacy_root = root / "generations"
        legacy_pointer = legacy_root / "active-generation.json"
        nested_generations = legacy_root / "generations"
        if (
            not canonical_pointer.exists()
            and legacy_pointer.is_file()
            and nested_generations.is_dir()
        ):
            children = tuple(nested_generations.iterdir())
            if all(not (legacy_root / child.name).exists() for child in children):
                for child in children:
                    child.replace(legacy_root / child.name)
                nested_generations.rmdir()
                legacy_pointer.replace(canonical_pointer)

        # ``customer-overlay.db`` used the current schema, while the 0.4.1
        # queue has a legacy schema imported by SQLiteCustomerOutbox. Rename a
        # single predecessor atomically; if both exist, prefer the newer schema
        # and import the old queue separately after opening the outbox.
        if not self.settings.outbox_path.exists():
            predecessor = next(
                (path for path in self.settings.legacy_outbox_paths if path.is_file()),
                None,
            )
            if predecessor is not None:
                predecessor.replace(self.settings.outbox_path)


def build_client(settings: ClientSettings | None = None) -> ClientContainer:
    if settings is not None:
        return ClientContainer(settings)
    environment = os.environ
    data_root = (
        Path(environment["PAPAGUI_CLIENT_DATA_ROOT"])
        if environment.get("PAPAGUI_CLIENT_DATA_ROOT")
        else ClientSettings().data_root
    )
    config_path = Path(
        environment.get("PAPAGUI_CLIENT_CONFIG_PATH", str(data_root / "client-config.json"))
    )
    repository = JsonClientConfigRepository(config_path, data_root=data_root)
    resolved = repository.resolve(environment)
    return ClientContainer(
        resolved.settings,
        config_repository=repository,
        config_sources=resolved.sources,
    )


def build_tray(settings: ClientSettings | None = None) -> ClientContainer:
    """Build the composition root that exposes remote server controls."""
    if settings is not None:
        return ClientContainer(settings, server_control=True)
    environment = os.environ
    data_root = (
        Path(environment["PAPAGUI_CLIENT_DATA_ROOT"])
        if environment.get("PAPAGUI_CLIENT_DATA_ROOT")
        else ClientSettings().data_root
    )
    config_path = Path(
        environment.get("PAPAGUI_CLIENT_CONFIG_PATH", str(data_root / "client-config.json"))
    )
    repository = JsonClientConfigRepository(config_path, data_root=data_root)
    resolved = repository.resolve(environment)
    return ClientContainer(
        resolved.settings,
        config_repository=repository,
        config_sources=resolved.sources,
        server_control=True,
    )
