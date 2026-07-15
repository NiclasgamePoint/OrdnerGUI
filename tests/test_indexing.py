from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.config import IndexOptions
from app.core.index_diagnostics import IndexDiagnosticsService
from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters


class IndexingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "Bauvorhaben"
        for topic in ("Blower Door", "Baubegleitung", "DEKRA", "Elektroplanung"):
            project = self.root / topic / "2026" / f"Musterkunde {topic}"
            project.mkdir(parents=True)
            (project / f"bericht-{topic}.txt").write_text(
                f"Prüftext aus dem Thema {topic}", encoding="utf-8"
            )
        self.database = Path(self.temp_dir.name) / "index.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_all_top_level_topics_are_indexed_generically(self):
        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)

        topics = {
            row[0]
            for row in manager.conn.execute(
                "SELECT DISTINCT domain_folder FROM files ORDER BY domain_folder"
            )
        }
        self.assertEqual(
            topics,
            {"Blower Door", "Baubegleitung", "DEKRA", "Elektroplanung"},
        )
        self.assertTrue(manager.search_folders("Musterkunde"))
        self.assertTrue(manager.search_in_text("Prüftext"))
        manager.close()

    def test_incremental_run_detects_no_changes_and_records_diagnostics(self):
        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)
        manager.synchronize_directory(self.root, full_rebuild=False)
        self.assertEqual(manager.last_change_count, 0)
        manager.close()

        diagnostics = IndexDiagnosticsService().inspect(self.database)
        self.assertEqual(diagnostics.integrity, "ok")
        self.assertEqual(diagnostics.file_count, 4)
        self.assertGreaterEqual(diagnostics.folder_count, 13)
        self.assertEqual(diagnostics.changed_count, 0)
        self.assertEqual(diagnostics.status_counts.get("success"), 4)

    def test_ranked_filtered_search_is_paginated(self):
        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)

        page = manager.search_files_page(
            "bericht",
            SearchFilters(domain_folder="DEKRA", year="2026", file_type="txt"),
            page=1,
            page_size=2,
        )
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0]["domain_folder"], "DEKRA")
        text_page = manager.search_text_page(
            "Prüftext",
            SearchFilters(domain_folder="Elektroplanung"),
            page=1,
            page_size=1,
        )
        self.assertEqual(text_page.total, 1)
        self.assertIn("Elektroplanung", text_page.items[0]["path"])
        manager.close()


if __name__ == "__main__":
    unittest.main()
