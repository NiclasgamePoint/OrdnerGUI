from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.index_manager import IndexManager


class IndexManagerContextTests(unittest.TestCase):
    def test_context_manager_closes_connection(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "index.db"
            manager = IndexManager(database)
            with manager as active:
                self.assertIs(active, manager)
                self.assertIsNotNone(manager.conn)
                self.assertEqual(
                    manager.conn.execute("SELECT 1").fetchone()[0],
                    1,
                )
            with self.assertRaises(Exception):
                manager.conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
