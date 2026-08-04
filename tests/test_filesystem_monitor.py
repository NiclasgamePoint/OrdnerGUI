from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.services.filesystem_monitor import FileSystemMonitor


class FileSystemMonitorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
