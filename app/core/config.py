import os
import shutil
from pathlib import Path
from dataclasses import asdict, dataclass
import hashlib
import json
from PySide6.QtCore import QSettings

from app.core.index_layout import IndexLayout


def _resolve_path_setting(env_name: str) -> Path | None:
    value = os.getenv(env_name, "").strip()
    if not value:
        return None
    # Keep a final `current` symlink intact: the client atomically switches it
    # when a verified server generation is activated.
    return Path(value).expanduser().absolute()


def _configure_settings_storage() -> None:
    """Use an explicit, portable QSettings directory when configured."""
    configured = _resolve_path_setting("PAPAGUI_SETTINGS_DIR")
    if configured is None:
        return

    configured.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for settings_format in (
        QSettings.Format.IniFormat,
        QSettings.Format.NativeFormat,
    ):
        QSettings.setPath(
            settings_format,
            QSettings.Scope.UserScope,
            str(configured),
        )
        QSettings.setPath(
            settings_format,
            QSettings.Scope.SystemScope,
            str(configured),
        )


def _resolve_base_dir() -> Path:
    configured = _resolve_path_setting("PAPAGUI_BASE_DIR")
    if configured is not None:
        return configured
    # Always derive defaults from source location, never from cwd.
    return Path(__file__).resolve().parents[2]


_configure_settings_storage()

BASE_DIR = _resolve_base_dir()
DATA_DIR = _resolve_path_setting("PAPAGUI_DATA_DIR") or (BASE_DIR / "data")
BAUVORHABEN_DIR = _resolve_path_setting("PAPAGUI_SOURCE_DIR") or (BASE_DIR / "Bauvorhaben")
INDEX_LAYOUT = IndexLayout(DATA_DIR / "index")
CATALOG_DB_FILE = INDEX_LAYOUT.catalog_path
LEGACY_INDEX_FILE = DATA_DIR / "index.db"
# Read the legacy index until the first split catalog has been activated.
DB_FILE = CATALOG_DB_FILE if CATALOG_DB_FILE.exists() else LEGACY_INDEX_FILE
CUSTOMER_DB_FILE = DATA_DIR / "customers.db"


def get_current_index_path() -> Path:
    return CATALOG_DB_FILE if CATALOG_DB_FILE.exists() else LEGACY_INDEX_FILE

INDEX_BATCH_SIZE = 100

WINDOW_TITLE = "PapaGUI - Kundenmanagement System"
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900

def forced_fullscreen() -> bool:
    value = os.getenv("PAPAGUI_FORCE_FULLSCREEN", "").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def external_indexer_enabled() -> bool:
    """Return whether indexing is owned by an external service."""
    value = os.getenv("PAPAGUI_EXTERNAL_INDEXER", "").strip().casefold()
    return value in {"1", "true", "yes", "on"}

RIPGREP_AVAILABLE = shutil.which("rg") is not None

SETTINGS_ORG = "PapaGUI"
SETTINGS_APP = "UI"
INDEX_SOURCE_KEY = "data/index_source"


@dataclass
class IndexOptions:
    automatic_monitoring_enabled: bool = True
    change_delay_seconds: int = 15
    daily_reconciliation_enabled: bool = True
    content_indexing_enabled: bool = True
    max_file_size_mb: int = 100
    max_extracted_characters: int = 2_000_000
    result_limit: int = 200
    ocr_enabled: bool = True
    ocr_max_pages: int = 5
    ocr_extended_max_pages: int = 25
    ocr_extension_threshold: int = 500
    ocr_timeout_seconds: int = 10
    pdf_text_timeout_seconds: int = 45
    content_extensions: str = "pdf,doc,docx,xls,xlsx,txt,csv,md,log,json,xml,yaml,yml,ini"
    excluded_folders: str = ".git,.venv,venv,__pycache__,node_modules"
    resource_profile: str = "balanced"
    preferred_document_patterns: str = "anschreiben,angebot,auftrag,vertrag"
    priority_documents_per_project: int = 24
    newest_years_first: bool = True
    content_search_enabled: bool = False
    maximum_parallel_shards: int = 4
    remote_sync_interval_minutes: int = 15

    @property
    def excluded_folder_names(self) -> set[str]:
        return {
            value.strip().casefold()
            for value in self.excluded_folders.split(",")
            if value.strip()
        }

    @property
    def indexed_content_types(self) -> set[str]:
        return {
            value.strip().lower().lstrip(".")
            for value in self.content_extensions.split(",")
            if value.strip()
        }

    @property
    def preferred_patterns(self) -> list[str]:
        return [
            value.strip().casefold()
            for value in self.preferred_document_patterns.split(",")
            if value.strip()
        ]

    @property
    def document_pause_seconds(self) -> float:
        return {"gentle": 0.25, "balanced": 0.05, "fast": 0.0}.get(
            self.resource_profile, 0.05
        )

    @property
    def ocr_workers(self) -> int:
        # Document-level concurrency owns the resource budget.  Nested OCR
        # pools would multiply that budget and make profile limits ineffective.
        return 1

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def catalog_fingerprint(self) -> str:
        payload = {
            "content_extensions": sorted(self.indexed_content_types),
            "excluded_folders": sorted(self.excluded_folder_names),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def content_fingerprint(self) -> str:
        payload = {
            "max_file_size_mb": self.max_file_size_mb,
            "max_extracted_characters": self.max_extracted_characters,
            "ocr_enabled": self.ocr_enabled,
            "ocr_max_pages": self.ocr_max_pages,
            "ocr_extended_max_pages": self.ocr_extended_max_pages,
            "ocr_extension_threshold": self.ocr_extension_threshold,
            "ocr_timeout_seconds": self.ocr_timeout_seconds,
            "pdf_text_timeout_seconds": self.pdf_text_timeout_seconds,
            "content_extensions": sorted(self.indexed_content_types),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class CustomerRecognitionOptions:
    enabled: bool = True
    minimum_year: int = 2016
    email_blacklist: str = ""
    phone_blacklist: str = ""
    name_blacklist: str = ""
    address_blacklist: str = ""
    text_blacklist: str = ""
    preferred_document_patterns: str = (
        "anschreiben,angebot,auftrag,auftragsbestätigung,brief,vertrag"
    )
    frequent_value_threshold: int = 5

    @staticmethod
    def _lines(value: str) -> list[str]:
        return [line.strip() for line in value.splitlines() if line.strip()]

    @property
    def emails(self) -> list[str]:
        return self._lines(self.email_blacklist)

    @property
    def phones(self) -> list[str]:
        return self._lines(self.phone_blacklist)

    @property
    def names(self) -> list[str]:
        return self._lines(self.name_blacklist)

    @property
    def addresses(self) -> list[str]:
        return self._lines(self.address_blacklist)

    @property
    def text_values(self) -> list[str]:
        return self._lines(self.text_blacklist)

    @property
    def preferred_patterns(self) -> list[str]:
        return [
            value.strip().casefold()
            for value in self.preferred_document_patterns.split(",")
            if value.strip()
        ]


def get_default_index_source() -> Path:
    """Standardquelle für die Indexierung (reale Projektdaten)."""
    return BAUVORHABEN_DIR


def get_configured_index_source() -> Path:
    """Return the persisted data source or the project default."""
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    stored_path = str(settings.value(INDEX_SOURCE_KEY, "")).strip()
    if not stored_path:
        return get_default_index_source()
    return Path(stored_path).expanduser()


def has_configured_index_source() -> bool:
    """Return whether the user explicitly selected a data source before."""
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return bool(str(settings.value(INDEX_SOURCE_KEY, "")).strip())


def save_index_source(path: Path):
    """Persist the selected data source in a platform-native settings store."""
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.setValue(INDEX_SOURCE_KEY, str(path))
    settings.sync()


def _setting_bool(settings: QSettings, key: str, default: bool) -> bool:
    return str(settings.value(key, default)).strip().casefold() in {
        "1", "true", "yes", "on"
    }


def load_index_options() -> IndexOptions:
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    defaults = IndexOptions()
    return IndexOptions(
        automatic_monitoring_enabled=_setting_bool(
            settings, "index/automatic_monitoring_enabled",
            defaults.automatic_monitoring_enabled,
        ),
        change_delay_seconds=int(settings.value(
            "index/change_delay_seconds", defaults.change_delay_seconds
        )),
        daily_reconciliation_enabled=_setting_bool(
            settings, "index/daily_reconciliation_enabled",
            defaults.daily_reconciliation_enabled,
        ),
        content_indexing_enabled=_setting_bool(
            settings, "index/content_indexing_enabled",
            defaults.content_indexing_enabled,
        ),
        max_file_size_mb=int(settings.value("index/max_file_size_mb", defaults.max_file_size_mb)),
        max_extracted_characters=int(
            settings.value("index/max_extracted_characters", defaults.max_extracted_characters)
        ),
        result_limit=int(settings.value("search/result_limit", defaults.result_limit)),
        ocr_enabled=_setting_bool(settings, "index/ocr_enabled", defaults.ocr_enabled),
        ocr_max_pages=int(settings.value("index/ocr_max_pages", defaults.ocr_max_pages)),
        ocr_extended_max_pages=int(settings.value(
            "index/ocr_extended_max_pages", defaults.ocr_extended_max_pages
        )),
        ocr_extension_threshold=int(settings.value(
            "index/ocr_extension_threshold", defaults.ocr_extension_threshold
        )),
        ocr_timeout_seconds=int(
            settings.value("index/ocr_timeout_seconds", defaults.ocr_timeout_seconds)
        ),
        pdf_text_timeout_seconds=int(settings.value(
            "index/pdf_text_timeout_seconds", defaults.pdf_text_timeout_seconds
        )),
        content_extensions=str(
            settings.value("index/content_extensions", defaults.content_extensions)
        ),
        excluded_folders=str(
            settings.value("index/excluded_folders", defaults.excluded_folders)
        ),
        resource_profile=str(settings.value(
            "index/resource_profile", defaults.resource_profile
        )),
        preferred_document_patterns=str(settings.value(
            "index/preferred_document_patterns", defaults.preferred_document_patterns
        )),
        priority_documents_per_project=int(settings.value(
            "index/priority_documents_per_project",
            defaults.priority_documents_per_project,
        )),
        newest_years_first=_setting_bool(
            settings, "index/newest_years_first", defaults.newest_years_first
        ),
        content_search_enabled=_setting_bool(
            settings, "search/content_enabled", defaults.content_search_enabled
        ),
        maximum_parallel_shards=int(settings.value(
            "search/maximum_parallel_shards", defaults.maximum_parallel_shards
        )),
        remote_sync_interval_minutes=max(1, int(settings.value(
            "sync/interval_minutes", defaults.remote_sync_interval_minutes
        ))),
    )


def save_index_options(options: IndexOptions):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    values = asdict(options)
    settings.setValue(
        "index/automatic_monitoring_enabled",
        values["automatic_monitoring_enabled"],
    )
    settings.setValue("index/change_delay_seconds", values["change_delay_seconds"])
    settings.setValue(
        "index/daily_reconciliation_enabled",
        values["daily_reconciliation_enabled"],
    )
    settings.setValue(
        "index/content_indexing_enabled", values["content_indexing_enabled"]
    )
    settings.setValue("index/max_file_size_mb", values["max_file_size_mb"])
    settings.setValue(
        "index/max_extracted_characters", values["max_extracted_characters"]
    )
    settings.setValue("search/result_limit", values["result_limit"])
    settings.setValue("index/ocr_enabled", values["ocr_enabled"])
    settings.setValue("index/ocr_max_pages", values["ocr_max_pages"])
    settings.setValue(
        "index/ocr_extended_max_pages", values["ocr_extended_max_pages"]
    )
    settings.setValue(
        "index/ocr_extension_threshold", values["ocr_extension_threshold"]
    )
    settings.setValue("index/ocr_timeout_seconds", values["ocr_timeout_seconds"])
    settings.setValue(
        "index/pdf_text_timeout_seconds", values["pdf_text_timeout_seconds"]
    )
    settings.setValue("index/content_extensions", values["content_extensions"])
    settings.setValue("index/excluded_folders", values["excluded_folders"])
    settings.setValue("index/resource_profile", values["resource_profile"])
    settings.setValue(
        "index/preferred_document_patterns",
        values["preferred_document_patterns"],
    )
    settings.setValue(
        "index/priority_documents_per_project",
        values["priority_documents_per_project"],
    )
    settings.setValue("index/newest_years_first", values["newest_years_first"])
    settings.setValue("search/content_enabled", values["content_search_enabled"])
    settings.setValue(
        "search/maximum_parallel_shards", values["maximum_parallel_shards"]
    )
    settings.setValue(
        "sync/interval_minutes", values["remote_sync_interval_minutes"]
    )
    settings.sync()
    status = settings.status()
    if int(getattr(status, "value", status)) != 0:
        raise OSError(
            f"Indexeinstellungen konnten nicht gespeichert werden: {settings.fileName()}"
        )


def load_customer_recognition_options() -> CustomerRecognitionOptions:
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    defaults = CustomerRecognitionOptions()
    return CustomerRecognitionOptions(
        enabled=str(
            settings.value("customer_recognition/enabled", defaults.enabled)
        ).lower() in {"1", "true", "yes"},
        minimum_year=2016,
        email_blacklist=str(settings.value("customer_recognition/email_blacklist", "")),
        phone_blacklist=str(settings.value("customer_recognition/phone_blacklist", "")),
        name_blacklist=str(settings.value("customer_recognition/name_blacklist", "")),
        address_blacklist=str(settings.value("customer_recognition/address_blacklist", "")),
        text_blacklist=str(settings.value("customer_recognition/text_blacklist", "")),
        preferred_document_patterns=str(settings.value(
            "customer_recognition/preferred_document_patterns",
            defaults.preferred_document_patterns,
        )),
        frequent_value_threshold=max(2, int(settings.value(
            "customer_recognition/frequent_value_threshold",
            defaults.frequent_value_threshold,
        ))),
    )


def save_customer_recognition_options(options: CustomerRecognitionOptions):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.setValue("customer_recognition/enabled", options.enabled)
    settings.setValue("customer_recognition/email_blacklist", options.email_blacklist)
    settings.setValue("customer_recognition/phone_blacklist", options.phone_blacklist)
    settings.setValue("customer_recognition/name_blacklist", options.name_blacklist)
    settings.setValue("customer_recognition/address_blacklist", options.address_blacklist)
    settings.setValue("customer_recognition/text_blacklist", options.text_blacklist)
    settings.setValue(
        "customer_recognition/preferred_document_patterns",
        options.preferred_document_patterns,
    )
    settings.setValue(
        "customer_recognition/frequent_value_threshold",
        options.frequent_value_threshold,
    )
    settings.sync()
