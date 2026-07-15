from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.filesystem_monitor import FileSystemMonitor


class FileSystemMonitorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
