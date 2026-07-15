from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
BAUVORHABEN_DIR = BASE_DIR / "Bauvorhaben"
DB_FILE = DATA_DIR / "index.db"

INDEX_BATCH_SIZE = 100

WINDOW_TITLE = "PapaGUI - Kundenmanagement System"
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900

RIPGREP_AVAILABLE = True


def get_default_index_source() -> Path:
	"""Standardquelle für die Indexierung (reale Projektdaten)."""
	return BAUVORHABEN_DIR
