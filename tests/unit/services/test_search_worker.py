from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtWidgets import QApplication

from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters
from app.gui.workers.search_worker import SearchWorker


class SearchWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_folder_search_completes_with_valid_category(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Bauvorhaben" / "DEKRA" / "2026" / "Muster"
            source.mkdir(parents=True)
            (source / "bericht.txt").write_text("Testinhalt", encoding="utf-8")
            db_path = root / "index.db"
            with IndexManager(db_path) as manager:
                manager.synchronize_directory(root / "Bauvorhaben", full_rebuild=True)

            worker = SearchWorker(
                db_path=db_path,
                generation=1,
                category="folders",
                query="Muster",
                result_limit=20,
                filters=SearchFilters(),
                page=1,
                page_size=20,
                customer_db_path=root / "customers.db",
            )
            payload = {}

            def on_completed(generation, category, results, error):
                payload["generation"] = generation
                payload["category"] = category
                payload["results"] = results
                payload["error"] = error

            worker.completed.connect(on_completed)
            worker.start()
            worker.wait(5000)
            self.app.processEvents()

            self.assertEqual(payload.get("generation"), 1)
            self.assertEqual(payload.get("category"), "folders")
            self.assertEqual(payload.get("error"), "")
            self.assertGreaterEqual(payload["results"].total, 1)


if __name__ == "__main__":
    unittest.main()
