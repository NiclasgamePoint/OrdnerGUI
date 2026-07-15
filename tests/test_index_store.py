from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.index_manager import IndexManager
from app.core.index_store import activate_index, available_backups, create_build_path


class IndexStoreTests(unittest.TestCase):
    def test_atomic_activation_keeps_three_backups(self):
        with TemporaryDirectory() as directory:
            active = Path(directory) / "index.db"
            for generation in range(5):
                build = create_build_path(active)
                manager = IndexManager(build)
                manager.set_metadata("built_at", f"2026-01-0{generation + 1}T12:00:00")
                manager.set_metadata("index_root", "/test")
                manager.conn.commit()
                manager.close()
                activate_index(active, build)
            self.assertTrue(active.exists())
            self.assertEqual(len(available_backups(active)), 3)


if __name__ == "__main__":
    unittest.main()
