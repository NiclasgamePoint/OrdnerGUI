"""Configuration für PapaGUI"""
from pathlib import Path

# Pfade
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
MOCK_DATA_DIR = DATA_DIR / "mock"
DB_FILE = DATA_DIR / "index.db"

# Index
INDEX_BATCH_SIZE = 100

# GUI
WINDOW_TITLE = "PapaGUI - Kundenmanagement System"
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900

# Volltextsuche
RIPGREP_AVAILABLE = True  # Wird bei Startup geprüft
