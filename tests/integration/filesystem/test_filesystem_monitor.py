from pathlib import Path
from tempfile import TemporaryDirectory
import builtins
import importlib.util
import runpy
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from app.services.filesystem_monitor import FileChangeSummary, FileSystemMonitor
from app.services.watchdog_support import load_watchdog


class FileSystemMonitorTests(unittest.TestCase):
    def test_optional_watchdog_import_fallback(self):
        original_import = builtins.__import__

        def import_without_watchdog(name, *args, **kwargs):
            if name.startswith("watchdog"):
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_watchdog):
            namespace = runpy.run_path("app/services/filesystem_monitor.py")
        self.assertIsNone(namespace["Observer"])
        self.assertIsNone(namespace["FileSystemEventHandler"])

        module_path = Path("app/services/filesystem_monitor.py").resolve()
        specification = importlib.util.spec_from_file_location(
            "filesystem_monitor_without_watchdog", module_path
        )
        module = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = module
        self.addCleanup(sys.modules.pop, specification.name, None)
        with patch("builtins.__import__", side_effect=import_without_watchdog):
            specification.loader.exec_module(module)
        self.assertIsNone(module.Observer)
        with patch("builtins.__import__", side_effect=import_without_watchdog):
            self.assertEqual(load_watchdog(), (None, None))
        handler, observer = load_watchdog()
        if importlib.util.find_spec("watchdog") is None:
            self.assertEqual((handler, observer), (None, None))
        else:
            self.assertIsNotNone(handler)
            self.assertIsNotNone(observer)

        events = ModuleType("watchdog.events")
        observers = ModuleType("watchdog.observers")
        events.FileSystemEventHandler = object
        observers.Observer = SimpleNamespace
        with patch.dict(
            sys.modules,
            {"watchdog.events": events, "watchdog.observers": observers},
        ):
            self.assertEqual(load_watchdog(), (object, SimpleNamespace))

    def test_summary_total_and_interval_lower_bound(self):
        self.assertEqual(FileChangeSummary(1, 2, 3).total, 6)
        with TemporaryDirectory() as directory:
            monitor = FileSystemMonitor(Path(directory), interval_seconds=0)
        self.assertEqual(monitor.interval_milliseconds, 500)

    def test_missing_root_is_reported_instead_of_looking_like_mass_deletion(self):
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "disconnected-drive"
            monitor = FileSystemMonitor(missing)

            with self.assertRaisesRegex(FileNotFoundError, "nicht erreichbar"):
                monitor.build_snapshot()

    def test_snapshot_detects_created_modified_and_deleted_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            first.write_text("alt", encoding="utf-8")
            monitor = FileSystemMonitor(root, interval_seconds=1)
            before = monitor.build_snapshot()

            first.write_text("neuer und längerer Inhalt", encoding="utf-8")
            second = root / "second.txt"
            second.write_text("neu", encoding="utf-8")
            after_create = monitor.build_snapshot()
            changes = monitor.compare_snapshots(before, after_create)
            self.assertEqual(changes.created, 1)
            self.assertEqual(changes.modified, 1)

            first.unlink()
            after_delete = monitor.build_snapshot()
            changes = monitor.compare_snapshots(after_create, after_delete)
            self.assertEqual(changes.deleted, 1)

    def test_excluded_directories_are_not_scanned(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ignored = root / ".git"
            ignored.mkdir()
            (ignored / "secret.txt").write_text("ignored", encoding="utf-8")
            monitor = FileSystemMonitor(root, excluded_folders={".git"})
            snapshot = monitor.build_snapshot()
            self.assertFalse(any("secret.txt" in path for path in snapshot))

    def test_snapshot_ignores_files_that_disappear_during_stat(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            monitor = FileSystemMonitor(root)
            original_stat = Path.stat

            def selective_stat(path, *args, **kwargs):
                if path.name == "gone.txt":
                    raise OSError("gone")
                return original_stat(path, *args, **kwargs)

            with (
                patch("app.services.filesystem_monitor.os.walk", return_value=[(str(root), [], ["gone.txt"])]),
                patch.object(Path, "stat", selective_stat),
            ):
                snapshot = monitor.build_snapshot()
        self.assertEqual(snapshot, {f"d:{root}": (0, 0)})

    def test_snapshot_reports_root_disappearing_during_walk(self):
        with TemporaryDirectory() as directory:
            monitor = FileSystemMonitor(Path(directory))
            with patch.object(Path, "exists", side_effect=[True, False]):
                with self.assertRaisesRegex(FileNotFoundError, "während der Prüfung"):
                    monitor.build_snapshot()

    def test_runtime_monitor_does_not_poll_the_complete_tree(self):
        with TemporaryDirectory() as directory:
            monitor = FileSystemMonitor(Path(directory), interval_seconds=0.5)
            with patch("app.services.filesystem_monitor.Observer", None), patch(
                "app.services.filesystem_monitor.FileSystemEventHandler", None
            ), patch.object(
                monitor,
                "build_snapshot",
                side_effect=AssertionError("Kein periodischer Vollscan erlaubt"),
            ) as snapshot:
                monitor.start()
                monitor.msleep(50)
                monitor.requestInterruption()
                monitor.wait(2000)
            snapshot.assert_not_called()

    def test_run_reports_missing_root(self):
        monitor = FileSystemMonitor(Path("missing"))
        errors = []
        monitor.scanFailed.connect(errors.append)
        monitor.run()
        self.assertIn("nicht erreichbar", errors[0])

    def test_native_monitor_debounces_events_and_stops_observer(self):
        class HandlerBase:
            pass

        observer = Mock()

        def start_observer():
            handler = observer.schedule.call_args.args[0]
            handler.on_created(SimpleNamespace(src_path="/root/new.txt"))
            handler.on_created(SimpleNamespace(src_path="/root/new.txt"))
            handler.on_modified(SimpleNamespace(src_path="/root/change.txt"))
            handler.on_deleted(SimpleNamespace(src_path="/root/old.txt"))
            handler.on_moved(SimpleNamespace(src_path="/root/from.txt", dest_path="/root/to.txt"))
            handler.on_created(SimpleNamespace(src_path="/root/.git/ignored.txt"))

        observer.start.side_effect = start_observer
        with TemporaryDirectory() as directory:
            monitor = FileSystemMonitor(Path(directory), excluded_folders={".git"})
            changes = []
            ready = []
            monitor.changesDetected.connect(changes.append)
            monitor.ready.connect(ready.append)
            with (
                patch("app.services.filesystem_monitor.Observer", return_value=observer),
                patch("app.services.filesystem_monitor.FileSystemEventHandler", HandlerBase),
                patch.object(monitor, "isInterruptionRequested", side_effect=[False, False, True]),
                patch.object(monitor, "msleep"),
            ):
                monitor.run()
        self.assertEqual(ready, [0])
        self.assertEqual(changes, [FileChangeSummary(created=2, modified=1, deleted=2)])
        observer.stop.assert_called_once_with()
        observer.join.assert_called_once_with(timeout=5)


if __name__ == "__main__":
    unittest.main()
