from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.core.index_manager import IndexManager
from app.core.index_store import create_build_path, seed_build_database, validate_index


class IndexingWorker(QThread):
    """Build or update a staged index without blocking the GUI."""

    progress = Signal(int, str)

    def __init__(self, db_path: Path, base_path: Path, options, full_rebuild: bool = False):
        super().__init__()
        self.db_path = db_path
        self.base_path = base_path
        self.options = options
        self.full_rebuild = full_rebuild
        self.error = ""
        self.indexed_count = 0
        self.changed_count = 0
        self.build_path = None
        self.no_changes = False
        self.cancelled = False

    def run(self):
        build_path = create_build_path(self.db_path)
        self.build_path = build_path
        manager = None
        try:
            seed_build_database(self.db_path, build_path, incremental=not self.full_rebuild)
            manager = IndexManager(build_path, options=self.options)
            self.indexed_count = manager.synchronize_directory(
                self.base_path,
                full_rebuild=self.full_rebuild,
                should_cancel=self.isInterruptionRequested,
                progress_callback=self.progress.emit,
            )
            self.changed_count = getattr(manager, "last_change_count", self.indexed_count)
            manager.close()
            manager = None
            validate_index(build_path)
            if self.changed_count == 0 and self.db_path.exists():
                build_path.unlink(missing_ok=True)
                self.build_path = None
                self.no_changes = True
        except Exception as exc:
            self.error = str(exc)
            self.cancelled = isinstance(exc, InterruptedError)
        finally:
            if manager is not None:
                manager.close()
            if self.error and build_path.exists():
                build_path.unlink(missing_ok=True)
                self.build_path = None
