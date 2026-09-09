"""Qt-free composition root for server processes and tests."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.adapters.catalog_reader import SqliteCatalogReader
from papagui_server.adapters.generations import GenerationV2Publisher
from papagui_server.adapters.migration import migrate_customer_database
from papagui_server.adapters.recognition import load_identity_policy
from papagui_server.adapters.security import ClientTokenAuthenticator
from papagui_server.adapters.run_state import JsonRunStateRepository
from papagui_server.adapters.source_guard import PersistentSourceIdentityGuard
from papagui_server.adapters.settings import JsonSettingsRepository
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.application.customers import CustomerApplicationService
from papagui_server.application.indexing import IndexAdminService, IndexRunCoordinator
from papagui_server.application.recognition import RecognitionApplicationService
from papagui_server.application.recognition_jobs import CustomerRecognitionJobs
from papagui_server.application.recognition_blocklist import RecognitionBlocklistApplicationService
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
    allow_insecure_no_client_token: bool = False
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
            allow_insecure_no_client_token=_environment_bool(
                "PAPAGUI_ALLOW_INSECURE_NO_AUTH"
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
    settings: SettingsApplicationService
    customers: CustomerApplicationService
    suggestions: CustomerSuggestionApplicationService
    recognition: RecognitionApplicationService
    recognition_jobs: CustomerRecognitionJobs
    recognition_blocklist: RecognitionBlocklistApplicationService
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
    def execute_recheck(customer_id, mode, cancelled):
        if mode == "rebuild":
            validate_recheck(None)
            coordinator.rebuild_recognition_documents(cancelled)
            if cancelled():
                raise InterruptedError
            recognition.synchronize(
                configuration.source_path, source_id=configuration.source_id,
                minimum_year=settings_repository.load().minimum_customer_year,
                cancelled=cancelled, exhaustive=True,
            )
            if cancelled():
                raise InterruptedError
            publisher.publish_all()
            return
        roots = recognition.customer_project_root_ids(customer_id)
        if mode == "extract" and roots:
            coordinator.extract_customer_documents(roots, cancelled)
        recognition.documents.run(configuration.source_id, customer_id=customer_id, cancelled=cancelled)
        if cancelled():
            raise InterruptedError
        if mode == "extract":
            publisher.publish_all()
        else:
            publisher.publish_customers()

    def validate_recheck(customer_id):
        if customer_id is None:
            current = settings_repository.load()
            if not current.recognition_pipeline_enabled or current.priority_documents_per_project == 0:
                raise ValueError("Bitte zuerst die Kundendatenerkennung in den Servereinstellungen aktivieren.")
            return
        recognition.customer_project_root_ids(customer_id)

    recognition_jobs = CustomerRecognitionJobs(
        store=JsonRunStateRepository(configuration.data_path / "jobs" / "customer-rechecks.json"),
        operation_lock=coordinator.operation_lock,
        validate=validate_recheck,
        execute=execute_recheck,
        document_worker_status=coordinator.document_worker_status,
    )
    settings = SettingsApplicationService(
        settings_repository, changed=coordinator.settings_changed
    )
    client_auth = ClientTokenAuthenticator(
        configuration.client_token,
        allow_insecure=configuration.allow_insecure_no_client_token,
    )
    return ServerContainer(
        configuration=configuration,
        client_auth=client_auth,
        settings=settings,
        customers=CustomerApplicationService(unit_of_work, publisher),
        suggestions=CustomerSuggestionApplicationService(unit_of_work, publisher),
        recognition=recognition,
        recognition_jobs=recognition_jobs,
        recognition_blocklist=RecognitionBlocklistApplicationService(
            unit_of_work, publisher,
        ),
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
