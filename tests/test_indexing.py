from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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
        with patch.object(manager, "_index_file", wraps=manager._index_file) as index_file:
            manager.synchronize_directory(self.root, full_rebuild=False)
            index_file.assert_not_called()
        self.assertEqual(manager.last_change_count, 0)
        manager.close()

        diagnostics = IndexDiagnosticsService().inspect(self.database)
        self.assertEqual(diagnostics.integrity, "ok")
        self.assertEqual(diagnostics.file_count, 4)
        self.assertGreaterEqual(diagnostics.folder_count, 13)
        self.assertEqual(diagnostics.changed_count, 0)
        self.assertEqual(diagnostics.status_counts.get("success"), 4)

    def test_incremental_run_processes_only_new_and_modified_files(self):
        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)
        changed_file = self.root / "DEKRA" / "2026" / "Musterkunde DEKRA" / "bericht-DEKRA.txt"
        changed_file.write_text("Geänderter und längerer Inhalt", encoding="utf-8")
        new_file = changed_file.parent / "neu.txt"
        new_file.write_text("Neue Datei", encoding="utf-8")

        with patch.object(manager, "_index_file", wraps=manager._index_file) as index_file:
            manager.synchronize_directory(self.root, full_rebuild=False)
            indexed_paths = {call.args[0] for call in index_file.call_args_list}
        self.assertEqual(indexed_paths, {changed_file, new_file})
        manager.close()

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

    def test_empty_source_directory_indexes_without_errors(self):
        empty_root = Path(self.temp_dir.name) / "Leer"
        empty_root.mkdir(parents=True, exist_ok=True)

        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        processed = manager.synchronize_directory(empty_root, full_rebuild=True)

        self.assertEqual(processed, 0)
        self.assertEqual(
            manager.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            0,
        )
        self.assertEqual(
            manager.conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0],
            1,
        )
        manager.close()


if __name__ == "__main__":
    unittest.main()
