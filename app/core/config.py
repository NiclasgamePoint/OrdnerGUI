from pathlib import Path
from PySide6.QtCore import QSettings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
BAUVORHABEN_DIR = BASE_DIR / "Bauvorhaben"
DB_FILE = DATA_DIR / "index.db"

INDEX_BATCH_SIZE = 100

WINDOW_TITLE = "PapaGUI - Kundenmanagement System"
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900

RIPGREP_AVAILABLE = True

SETTINGS_ORG = "PapaGUI"
SETTINGS_APP = "UI"
INDEX_SOURCE_KEY = "data/index_source"


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
