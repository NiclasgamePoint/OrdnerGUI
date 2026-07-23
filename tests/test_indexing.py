from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.core.config import IndexOptions
from app.core.index_diagnostics import IndexDiagnosticsService
from app.core import fuzzy_search
from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters, SearchSort


class IndexingTests(unittest.TestCase):
    def test_index_exposes_document_text_with_source_boundaries(self):
        project = self.root / "DEKRA" / "2026" / "Dokumentgrenzen, Berlin"
        project.mkdir(parents=True)
        (project / "Anschreiben.txt").write_text("Erster Text", encoding="utf-8")
        (project / "Vertrag.txt").write_text("Zweiter Text", encoding="utf-8")
        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)

        documents = manager.indexed_documents_for_folder(str(project.resolve()))

        self.assertEqual(
            {item["filename"] for item in documents},
            {"Anschreiben.txt", "Vertrag.txt"},
        )
        self.assertEqual(
            {item["content"] for item in documents},
            {"Erster Text", "Zweiter Text"},
        )
        manager.close()

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
        self.assertEqual(indexed_paths, {changed_file.resolve(), new_file.resolve()})
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

    def test_folder_results_support_relevance_date_and_alphabet_sorting(self):
        projects = [
            self.root / "DEKRA" / "2025" / "Sortierkunde Alpha, Berlin",
            self.root / "DEKRA" / "2026" / "Sortierkunde Zulu, Berlin",
        ]
        for project in projects:
            project.mkdir(parents=True)
            (project / "datei.txt").write_text("Sortierung", encoding="utf-8")
        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)

        relevant = manager.search_folders_page(
            "Sortierkunde",
            SearchFilters(sort_order=SearchSort.RELEVANCE),
            page_size=10,
        )
        alphabetical = manager.search_folders_page(
            "Sortierkunde",
            SearchFilters(sort_order=SearchSort.ALPHABETICAL),
            page_size=10,
        )
        newest = manager.search_folders_page(
            "Sortierkunde",
            SearchFilters(sort_order=SearchSort.DATE),
            page_size=10,
        )

        self.assertEqual(relevant.total, 2)
        self.assertIn("Alpha", alphabetical.items[0]["folder_name"])
        self.assertIn("Zulu", newest.items[0]["folder_name"])
        manager.close()

    def test_fts_snippets_include_document_metadata_and_sorting(self):
        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)
        manager.conn.execute(
            "UPDATE files SET modified_date='2024-01-01T00:00:00' "
            "WHERE filename LIKE '%Blower Door%'"
        )
        manager.conn.execute(
            "UPDATE files SET modified_date='2099-01-01T00:00:00' "
            "WHERE filename LIKE '%DEKRA%'"
        )
        manager.conn.commit()

        relevance = manager.search_text_page(
            "Prüftext",
            SearchFilters(sort_order=SearchSort.RELEVANCE),
            page_size=10,
        )
        alphabetical = manager.search_text_page(
            "Prüftext",
            SearchFilters(sort_order=SearchSort.ALPHABETICAL),
            page_size=10,
        )
        newest = manager.search_text_page(
            "Prüftext",
            SearchFilters(sort_order=SearchSort.DATE),
            page_size=10,
        )

        self.assertEqual(relevance.total, 4)
        self.assertIn("Prüftext", relevance.items[0]["excerpt"])
        self.assertTrue(relevance.items[0]["filename"].endswith(".txt"))
        self.assertTrue(relevance.items[0]["folder_path"])
        self.assertLessEqual(
            alphabetical.items[0]["filename"].casefold(),
            alphabetical.items[1]["filename"].casefold(),
        )
        self.assertIn("DEKRA", newest.items[0]["filename"])
        manager.close()

    def test_fuzzy_multiword_folder_search_uses_name_city_and_service(self):
        target = self.root / "Blower Door" / "2025" / "Wahnhorst, Kaltenkirchen"
        target.mkdir(parents=True)
        (target / "messung.txt").write_text("Messdaten", encoding="utf-8")
        distractor = self.root / "DEKRA" / "2025" / "Horst Beispiel, Hamburg"
        distractor.mkdir(parents=True)
        (distractor / "bericht.txt").write_text("Bericht", encoding="utf-8")

        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)

        page = manager.search_folders_page(
            "Horst Kaltenkirchn", SearchFilters(), page=1, page_size=10
        )
        filtered = manager.search_folders_page(
            "Horst Kaltenkirchen",
            SearchFilters(domain_folder="Blower Door", year="2025", file_type="txt"),
            page=1,
            page_size=10,
        )

        self.assertGreaterEqual(page.total, 1)
        self.assertEqual(page.items[0]["folder_path"], str(target.resolve()))
        self.assertEqual(filtered.total, 1)
        self.assertEqual(filtered.items[0]["folder_path"], str(target.resolve()))
        manager.close()

    def test_fuzzy_folder_search_scores_at_most_five_candidates(self):
        for number in range(10):
            candidate = self.root / "DEKRA" / "2025" / f"Kandidat {number}, Berlin"
            candidate.mkdir(parents=True)
            (candidate / "bericht.txt").write_text("Bericht", encoding="utf-8")

        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)

        with patch.object(
            fuzzy_search,
            "fuzzy_record_score",
            wraps=fuzzy_search.fuzzy_record_score,
        ) as scorer:
            manager.search_folders_page(
                "Kanddat Berln", SearchFilters(), page=1, page_size=10
            )

        self.assertLessEqual(scorer.call_count, 5)
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

    def test_structured_subfolders_collapse_to_project_root_but_legacy_stays_visible(self):
        current_project = self.root / "DEKRA" / "2026" / "Müller, Berlin"
        pictures = current_project / "Bilder"
        empty = current_project / "Dokumente" / "Leer"
        pictures.mkdir(parents=True)
        empty.mkdir(parents=True)
        (pictures / "foto.txt").write_text("Foto", encoding="utf-8")
        legacy = self.root / "DEKRA" / "2015" / "Altprojekt" / "Scans"
        legacy.mkdir(parents=True)
        (legacy / "alt.txt").write_text("Alt", encoding="utf-8")

        manager = IndexManager(self.database, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(self.root, full_rebuild=True)

        structured_hidden = manager.search_folders_page(
            "Bilder", SearchFilters(), page=1, page_size=25
        )
        structured_visible = manager.search_folders_page(
            "Bilder",
            SearchFilters(include_subfolders=True),
            page=1,
            page_size=25,
        )
        legacy_result = manager.search_folders_page(
            "Scans", SearchFilters(), page=1, page_size=25
        )
        details = manager.get_folder_details(str(pictures))

        self.assertEqual(structured_hidden.total, 0)
        self.assertEqual(structured_visible.total, 1)
        self.assertEqual(
            structured_visible.items[0]["folder_path"], str(current_project.resolve())
        )
        self.assertEqual(legacy_result.total, 1)
        self.assertEqual(legacy_result.items[0]["folder_path"], str(legacy.resolve()))
        self.assertEqual(details["folder_path"], str(current_project.resolve()))
        self.assertEqual(
            {node["name"] for node in details["subfolders"]},
            {"Bilder", "Dokumente"},
        )
        documents = next(
            node for node in details["subfolders"] if node["name"] == "Dokumente"
        )
        self.assertEqual(documents["children"][0]["name"], "Leer")
        manager.close()


if __name__ == "__main__":
    unittest.main()
