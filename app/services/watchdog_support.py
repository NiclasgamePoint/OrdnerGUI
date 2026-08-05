from __future__ import annotations


def load_watchdog():
    """Return watchdog classes when installed, otherwise a portable fallback."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return None, None
    return FileSystemEventHandler, Observer
