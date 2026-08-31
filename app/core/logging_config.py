from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import DATA_DIR


LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "papagui.log"
CONTENT_PROCESS_LOG_FILE = LOG_DIR / "content-index-process.log"


def open_content_process_log():
    """Open the durable stream used for detached content-worker output."""
    CONTENT_PROCESS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    return CONTENT_PROCESS_LOG_FILE.open(
        "a", encoding="utf-8", errors="replace", buffering=1
    )


def configure_logging(log_file: Path = LOG_FILE) -> Path:
    """Configure application-wide console and rotating file logging once."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(handler, "_papagui_handler", False) for handler in root.handlers):
        return log_file

    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"
    )
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._papagui_handler = True
    root.addHandler(file_handler)
    # Some repairable PDFs contain duplicate dictionary keys. pypdf can read
    # them, but emits one warning per duplicate and can bury actionable logs.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return log_file
