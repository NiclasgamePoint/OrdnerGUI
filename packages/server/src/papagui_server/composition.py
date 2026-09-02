"""Qt-free composition root for server processes and tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import os
from pathlib import Path

from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.adapters.catalog_reader import SqliteCatalogReader
from papagui_server.adapters.generations import GenerationV2Publisher
from papagui_server.adapters.migration import migrate_customer_database
from papagui_server.adapters.recognition import load_identity_policy
from papagui_server.adapters.security import (
    AdminSessionManager,
    ClientTokenAuthenticator,
    SecurityConfigurationStore,
)
from papagui_server.adapters.run_state import JsonRunStateRepository
from papagui_server.adapters.source_guard import PersistentSourceIdentityGuard
from papagui_server.adapters.settings import JsonSettingsRepository
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.application.customers import CustomerApplicationService
from papagui_server.application.indexing import IndexAdminService, IndexRunCoordinator
from papagui_server.application.recognition import RecognitionApplicationService
from papagui_server.application.settings import SettingsApplicationService
from papagui_server.application.suggestions import CustomerSuggestionApplicationService
from papagui_server.domain.models import ServerSettings


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    source_path: Path
    data_path: Path
    config_path: Path
    source_id: str = "primary"
    client_token: str = ""
    admin_password_hash: str = ""
    bootstrap_admin_password: str = ""
    allow_insecure_no_client_token: bool = False
    admin_session_minutes: int = 480
    default_interval_seconds: int = 86_400
    initialize_source_identity: bool = True

    @classmethod
    def from_environment(cls) -> "RuntimeConfiguration":
        return cls(
            source_path=Path(os.getenv("PAPAGUI_SOURCE_DIR", "/source")),
            data_path=Path(os.getenv("PAPAGUI_DATA_DIR", "/data")),
            config_path=Path(os.getenv("PAPAGUI_CONFIG_DIR", "/config")),
            source_id=os.getenv("PAPAGUI_SOURCE_ID", "primary"),
            client_token=_environment_secret(
                "PAPAGUI_API_TOKEN", "PAPAGUI_API_TOKEN_FILE"
            ),
            admin_password_hash=_environment_secret(
                "PAPAGUI_ADMIN_PASSWORD_HASH",
                "PAPAGUI_ADMIN_PASSWORD_HASH_FILE",
            ),
            bootstrap_admin_password=os.getenv("PAPAGUI_ADMIN_PASSWORD", ""),
            allow_insecure_no_client_token=_environment_bool(
                "PAPAGUI_ALLOW_INSECURE_NO_AUTH"
            ),
            admin_session_minutes=int(
                os.getenv("PAPAGUI_ADMIN_SESSION_MINUTES", "480")
            ),
            default_interval_seconds=int(
                os.getenv("PAPAGUI_INDEX_INTERVAL_SECONDS", "86400")
            ),
            initialize_source_identity=_environment_bool(
                "PAPAGUI_INITIALIZE_SOURCE_IDENTITY", default=True
            ),
        )


@dataclass(slots=True)
class ServerContainer:
    configuration: RuntimeConfiguration
    client_auth: ClientTokenAuthenticator
    admin_sessions: AdminSessionManager
    settings: SettingsApplicationService
    customers: CustomerApplicationService
    suggestions: CustomerSuggestionApplicationService
    recognition: RecognitionApplicationService
    catalog: SqliteCatalogReader
    index_admin: IndexAdminService
    coordinator: IndexRunCoordinator
    publisher: GenerationV2Publisher


def build_container(configuration: RuntimeConfiguration) -> ServerContainer:
    configuration.data_path.mkdir(parents=True, exist_ok=True)
    configuration.config_path.mkdir(parents=True, exist_ok=True)
    customer_database = configuration.data_path / "customers.db"
    migrate_customer_database(
        customer_database,
        configuration.config_path / "migration-state.json",
    )
    unit_of_work = SqliteCustomerUnitOfWorkFactory(customer_database)
    publisher = GenerationV2Publisher(configuration.data_path)
    settings_repository = JsonSettingsRepository(
        configuration.config_path / "server-settings.json",
        defaults=ServerSettings(interval_seconds=configuration.default_interval_seconds),
    )
    catalog_reader = SqliteCatalogReader(
        configuration.data_path / "index" / "catalog" / "active.db"
    )
    recognition = RecognitionApplicationService(
        unit_of_work,
        catalog_reader,
        publisher,
        load_identity_policy(),
        source_id=configuration.source_id,
        documents_per_project=settings_repository.load().priority_documents_per_project,
        settings=settings_repository,
    )
    coordinator = IndexRunCoordinator(
        source_path=configuration.source_path,
        source_id=configuration.source_id,
        data_path=configuration.data_path,
        settings=settings_repository,
        catalog=SqliteCatalogIndexer(configuration.data_path),
        recognizer=recognition,
        publisher=publisher,
        run_state=JsonRunStateRepository(
            configuration.data_path / "jobs" / "server-status.json"
        ),
        source_guard=PersistentSourceIdentityGuard(
            configuration.config_path / "source-identity.json",
            allow_initialize=configuration.initialize_source_identity,
        ),
    )
    settings = SettingsApplicationService(
        settings_repository, changed=coordinator.settings_changed
    )
    security_store = SecurityConfigurationStore(
        configuration.config_path / "security.json"
    )
    password_hash = security_store.initialize(
        configured_hash=configuration.admin_password_hash,
        bootstrap_password=configuration.bootstrap_admin_password,
    )
    client_auth = ClientTokenAuthenticator(
        configuration.client_token,
        allow_insecure=configuration.allow_insecure_no_client_token,
    )
    admin_sessions = AdminSessionManager(
        password_hash,
        ttl=timedelta(minutes=configuration.admin_session_minutes),
    )
    return ServerContainer(
        configuration=configuration,
        client_auth=client_auth,
        admin_sessions=admin_sessions,
        settings=settings,
        customers=CustomerApplicationService(unit_of_work, publisher),
        suggestions=CustomerSuggestionApplicationService(unit_of_work, publisher),
        recognition=recognition,
        catalog=catalog_reader,
        index_admin=IndexAdminService(coordinator),
        coordinator=coordinator,
        publisher=publisher,
    )


def _environment_bool(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _environment_secret(value_name: str, file_name: str) -> str:
    direct = os.getenv(value_name, "").strip()
    if direct:
        return direct
    configured_file = os.getenv(file_name, "").strip()
    if not configured_file:
        return ""
    path = Path(configured_file)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(f"{file_name} kann nicht gelesen werden: {path}") from error
    if not value:
        raise ValueError(f"{file_name} verweist auf eine leere Datei: {path}")
    return value
