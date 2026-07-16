from pathlib import Path
from dataclasses import asdict, dataclass
import hashlib
import json
from PySide6.QtCore import QSettings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
BAUVORHABEN_DIR = BASE_DIR / "Bauvorhaben"
DB_FILE = DATA_DIR / "index.db"
CUSTOMER_DB_FILE = DATA_DIR / "customers.db"

INDEX_BATCH_SIZE = 100

WINDOW_TITLE = "PapaGUI - Kundenmanagement System"
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900

RIPGREP_AVAILABLE = True

SETTINGS_ORG = "PapaGUI"
SETTINGS_APP = "UI"
INDEX_SOURCE_KEY = "data/index_source"


@dataclass
class IndexOptions:
    max_file_size_mb: int = 100
    max_extracted_characters: int = 2_000_000
    result_limit: int = 200
    ocr_enabled: bool = True
    ocr_max_pages: int = 5
    ocr_timeout_seconds: int = 10
    content_extensions: str = "pdf,doc,docx,xls,xlsx,txt,csv,md,log,json,xml,yaml,yml,ini"
    excluded_folders: str = ".git,.venv,venv,__pycache__,node_modules"

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

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class CustomerRecognitionOptions:
    enabled: bool = False
    minimum_year: int = 2016
    email_blacklist: str = ""
    phone_blacklist: str = ""
    name_blacklist: str = ""
    address_blacklist: str = ""
    text_blacklist: str = ""

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


def save_index_source(path: Path):
    """Persist the selected data source in a platform-native settings store."""
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.setValue(INDEX_SOURCE_KEY, str(path))
    settings.sync()


def load_index_options() -> IndexOptions:
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    defaults = IndexOptions()
    return IndexOptions(
        max_file_size_mb=int(settings.value("index/max_file_size_mb", defaults.max_file_size_mb)),
        max_extracted_characters=int(
            settings.value("index/max_extracted_characters", defaults.max_extracted_characters)
        ),
        result_limit=int(settings.value("search/result_limit", defaults.result_limit)),
        ocr_enabled=str(settings.value("index/ocr_enabled", defaults.ocr_enabled)).lower()
        in {"1", "true", "yes"},
        ocr_max_pages=int(settings.value("index/ocr_max_pages", defaults.ocr_max_pages)),
        ocr_timeout_seconds=int(
            settings.value("index/ocr_timeout_seconds", defaults.ocr_timeout_seconds)
        ),
        content_extensions=str(
            settings.value("index/content_extensions", defaults.content_extensions)
        ),
        excluded_folders=str(
            settings.value("index/excluded_folders", defaults.excluded_folders)
        ),
    )


def save_index_options(options: IndexOptions):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    values = asdict(options)
    settings.setValue("index/max_file_size_mb", values["max_file_size_mb"])
    settings.setValue(
        "index/max_extracted_characters", values["max_extracted_characters"]
    )
    settings.setValue("search/result_limit", values["result_limit"])
    settings.setValue("index/ocr_enabled", values["ocr_enabled"])
    settings.setValue("index/ocr_max_pages", values["ocr_max_pages"])
    settings.setValue("index/ocr_timeout_seconds", values["ocr_timeout_seconds"])
    settings.setValue("index/content_extensions", values["content_extensions"])
    settings.setValue("index/excluded_folders", values["excluded_folders"])
    settings.sync()


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
    )


def save_customer_recognition_options(options: CustomerRecognitionOptions):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.setValue("customer_recognition/enabled", options.enabled)
    settings.setValue("customer_recognition/email_blacklist", options.email_blacklist)
    settings.setValue("customer_recognition/phone_blacklist", options.phone_blacklist)
    settings.setValue("customer_recognition/name_blacklist", options.name_blacklist)
    settings.setValue("customer_recognition/address_blacklist", options.address_blacklist)
    settings.setValue("customer_recognition/text_blacklist", options.text_blacklist)
    settings.sync()
