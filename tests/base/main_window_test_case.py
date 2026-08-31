"""Safe runtime boundaries for tests that construct :class:`MainWindow`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.core.index_layout import IndexLayout
from tests.base.qt_test_case import QtTestCase


class _TestSignal:
    """Small signal stand-in that keeps constructor wiring testable."""

    def __init__(self) -> None:
        self.slots = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def emit(self, *args, **kwargs) -> None:
        for slot in tuple(self.slots):
            slot(*args, **kwargs)


class InertIndexJobController:
    """Record catalog-job requests without creating a subprocess or timer."""

    def __init__(
        self,
        active_path: Path,
        state_dir: Path,
        customer_database_path: Path | None = None,
        parent=None,
    ) -> None:
        self.active_path = Path(active_path)
        self.state_dir = Path(state_dir)
        self.customer_database_path = customer_database_path
        self.parent = parent
        self.progress = _TestSignal()
        self.ready = _TestSignal()
        self.finished = _TestSignal()
        self.start_calls: list[tuple[Path, bool]] = []
        self.adopt_calls = 0
        self.cancel_calls = 0
        self.poll_calls = 0
        self.release_owner_calls = 0
        self.acknowledge_activation_calls = 0
        self.state: dict = {}

    def adopt_running_job(self) -> bool:
        self.adopt_calls += 1
        return False

    def start(self, source: Path, full_rebuild: bool) -> bool:
        self.start_calls.append((Path(source), bool(full_rebuild)))
        return False

    def poll(self) -> None:
        self.poll_calls += 1

    def is_active(self) -> bool:
        return False

    def current_state(self) -> dict:
        return dict(self.state)

    def acknowledge_activation(self) -> None:
        self.acknowledge_activation_calls += 1

    def cancel(self) -> None:
        self.cancel_calls += 1

    def release_owner(self) -> None:
        self.release_owner_calls += 1


class InertContentJobController:
    """Record content-job requests without adopting or spawning a process."""

    def __init__(
        self,
        layout: IndexLayout,
        customer_database_path: Path,
        parent=None,
    ) -> None:
        self.layout = layout
        self.customer_database_path = Path(customer_database_path)
        self.parent = parent
        self.state_dir = layout.jobs_dir / "content"
        self.progress = _TestSignal()
        self.finished = _TestSignal()
        self.start_calls = 0
        self.cancel_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self.stop_observing_calls = 0

    def start_or_adopt(self) -> bool:
        self.start_calls += 1
        return False

    def is_active(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancel_calls += 1

    def pause(self) -> None:
        self.pause_calls += 1

    def resume(self) -> bool:
        self.resume_calls += 1
        return False

    def stop_observing(self) -> None:
        self.stop_observing_calls += 1


class InertFileSystemMonitor:
    """Behave like a running monitor without owning a native/Python thread."""

    def __init__(self, source: Path, *args, **kwargs) -> None:
        self.source = Path(source)
        self.args = args
        self.kwargs = kwargs
        self.changesDetected = _TestSignal()
        self.scanFailed = _TestSignal()
        self.start_calls = 0
        self.request_interruption_calls = 0
        self.wait_calls = 0
        self.delete_later_calls = 0
        self._running = False

    def start(self) -> None:
        self.start_calls += 1
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def requestInterruption(self) -> None:
        self.request_interruption_calls += 1

    def wait(self) -> bool:
        self.wait_calls += 1
        self._running = False
        return True

    def deleteLater(self) -> None:
        self.delete_later_calls += 1


class InertStatisticsWorker:
    """Keep statistics refresh lifecycle intact without starting a QThread."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.completed = _TestSignal()
        self.finished = _TestSignal()
        self.start_calls = 0
        self.request_interruption_calls = 0
        self.wait_calls = 0
        self.delete_later_calls = 0
        self._running = False

    def start(self) -> None:
        self.start_calls += 1
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def requestInterruption(self) -> None:
        self.request_interruption_calls += 1

    def wait(self) -> bool:
        self.wait_calls += 1
        self._running = False
        return True

    def deleteLater(self) -> None:
        self.delete_later_calls += 1


class MainWindowTestCase(QtTestCase):
    """Isolate MainWindow storage and every automatic background boundary."""

    def setUp(self) -> None:
        super().setUp()

        from app.gui import main_window

        self.main_window_data_path = self.temp_path / "app-data"
        self.main_window_source_path = self.temp_path / "source"
        self.main_window_source_path.mkdir(parents=True)
        self.main_window_index_layout = IndexLayout(
            self.main_window_data_path / "index"
        )
        self.main_window_catalog_path = self.main_window_index_layout.catalog_path
        self.main_window_database_path = self.main_window_data_path / "index.db"
        self.main_window_customer_database_path = (
            self.main_window_data_path / "customers.db"
        )
        self.main_window_preview_cache_path = (
            self.main_window_data_path / "preview_cache" / "word"
        )

        replacements = {
            "INDEX_LAYOUT": self.main_window_index_layout,
            "CATALOG_DB_FILE": self.main_window_catalog_path,
            "DB_FILE": self.main_window_database_path,
            "CUSTOMER_DB_FILE": self.main_window_customer_database_path,
            "IndexJobController": InertIndexJobController,
            "ContentJobController": InertContentJobController,
            "FileSystemMonitor": InertFileSystemMonitor,
            "StatisticsWorker": InertStatisticsWorker,
        }
        for name, replacement in replacements.items():
            active_patch = self.enterContext(patch.object(main_window, name, replacement))
            self.assertIs(active_patch, replacement)

        self.enterContext(
            patch.object(
                main_window,
                "get_configured_index_source",
                return_value=self.main_window_source_path,
            )
        )
        self.enterContext(
            patch.object(
                main_window, "has_configured_index_source", return_value=True
            )
        )
        self.enterContext(
            patch.object(
                main_window.DocumentConverter,
                "WORD_PREVIEW_CACHE_DIR",
                self.main_window_preview_cache_path,
            )
        )
