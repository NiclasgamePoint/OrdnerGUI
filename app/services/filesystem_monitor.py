from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # Optional until dependencies are installed after an update.
    FileSystemEventHandler = None
    Observer = None


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileChangeSummary:
    created: int = 0
    modified: int = 0
    deleted: int = 0

    @property
    def total(self) -> int:
        return self.created + self.modified + self.deleted


class FileSystemMonitor(QThread):
    """Debounce native filesystem events without rescanning the whole source."""

    changesDetected = Signal(object)
    scanFailed = Signal(str)
    ready = Signal(int)

    def __init__(
        self,
        root_path: Path,
        excluded_folders: set[str] | None = None,
        interval_seconds: float = 12.0,
        parent=None,
    ):
        super().__init__(parent)
        self.root_path = root_path.resolve()
        self.excluded_folders = excluded_folders or set()
        self.interval_milliseconds = max(500, int(interval_seconds * 1000))
        self._snapshot: dict[str, tuple[int, int]] | None = None

    def build_snapshot(self) -> dict[str, tuple[int, int]]:
        if not self.root_path.exists() or not self.root_path.is_dir():
            raise FileNotFoundError(
                f"Datenquelle nicht erreichbar: {self.root_path}"
            )
        snapshot: dict[str, tuple[int, int]] = {}
        for current_root, directory_names, file_names in os.walk(self.root_path):
            directory_names[:] = [
                name
                for name in directory_names
                if name.casefold() not in self.excluded_folders
            ]
            current_path = Path(current_root)
            snapshot[f"d:{current_path}"] = (0, 0)
            for filename in file_names:
                path = current_path / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                snapshot[f"f:{path}"] = (stat.st_size, stat.st_mtime_ns)
        # os.walk() reports neither an error nor an incomplete result when its
        # root disappears during a scan (notably with removable/network drives).
        # Never turn such a transient outage into thousands of deletions.
        if not self.root_path.exists() or not self.root_path.is_dir():
            raise FileNotFoundError(
                f"Datenquelle während der Prüfung nicht mehr erreichbar: {self.root_path}"
            )
        return snapshot

    @staticmethod
    def compare_snapshots(
        previous: dict[str, tuple[int, int]], current: dict[str, tuple[int, int]]
    ) -> FileChangeSummary:
        old_paths = set(previous)
        new_paths = set(current)
        shared = old_paths & new_paths
        return FileChangeSummary(
            created=len(new_paths - old_paths),
            modified=sum(previous[path] != current[path] for path in shared),
            deleted=len(old_paths - new_paths),
        )

    def run(self):
        if not self.root_path.exists() or not self.root_path.is_dir():
            self.scanFailed.emit(f"Datenquelle nicht erreichbar: {self.root_path}")
            return
        if Observer is None or FileSystemEventHandler is None:
            logger.warning(
                "Native Dateisystemüberwachung ist nicht verfügbar; "
                "der tägliche Katalogabgleich bleibt aktiv."
            )
            self.ready.emit(0)
            while not self.isInterruptionRequested():
                self.msleep(500)
            return

        lock = threading.Lock()
        counters = {"created": set(), "modified": set(), "deleted": set()}

        class Handler(FileSystemEventHandler):
            def _record(self, kind: str, event):
                path = Path(event.src_path)
                if any(part.casefold() in self_excluded for part in path.parts):
                    return
                with lock:
                    counters[kind].add(str(path))

            def on_created(self, event):
                self._record("created", event)

            def on_modified(self, event):
                self._record("modified", event)

            def on_deleted(self, event):
                self._record("deleted", event)

            def on_moved(self, event):
                self._record("deleted", event)
                with lock:
                    counters["created"].add(str(event.dest_path))

        self_excluded = self.excluded_folders
        observer = Observer()
        observer.schedule(Handler(), str(self.root_path), recursive=True)
        observer.start()
        self.ready.emit(0)
        try:
            while not self.isInterruptionRequested():
                self.msleep(max(500, self.interval_milliseconds))
                with lock:
                    changes = FileChangeSummary(
                        created=len(counters["created"]),
                        modified=len(counters["modified"]),
                        deleted=len(counters["deleted"]),
                    )
                    for values in counters.values():
                        values.clear()
                if changes.total:
                    logger.info(
                        "Dateisystemereignisse: root=%s created=%s modified=%s deleted=%s",
                        self.root_path, changes.created, changes.modified, changes.deleted,
                    )
                    self.changesDetected.emit(changes)
        finally:
            observer.stop()
            observer.join(timeout=5)
