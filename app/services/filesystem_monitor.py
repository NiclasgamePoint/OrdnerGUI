from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path

from PySide6.QtCore import QThread, Signal


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
    """Portable polling monitor suitable for local folders and network shares."""

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
        while not self.isInterruptionRequested():
            try:
                current = self.build_snapshot()
                if self._snapshot is None:
                    self._snapshot = current
                    self.ready.emit(len(current))
                else:
                    changes = self.compare_snapshots(self._snapshot, current)
                    self._snapshot = current
                    if changes.total:
                        logger.info(
                            "Dateisystemänderung: root=%s created=%s modified=%s deleted=%s",
                            self.root_path,
                            changes.created,
                            changes.modified,
                            changes.deleted,
                        )
                        self.changesDetected.emit(changes)
            except Exception as exc:
                logger.exception("Dateisystemüberwachung fehlgeschlagen: root=%s", self.root_path)
                self.scanFailed.emit(str(exc))

            waited = 0
            while waited < self.interval_milliseconds and not self.isInterruptionRequested():
                step = min(500, self.interval_milliseconds - waited)
                self.msleep(step)
                waited += step
