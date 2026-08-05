from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock, patch

from app.core.config import IndexOptions
from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters, SearchSort
from tests.base.test_case import PapaGuiTestCase


class IndexManagerEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.temp_path / "source"
        self.project = self.source / "Service" / "2026" / "Customer, Berlin"
        self.project.mkdir(parents=True)
        self.document = self.project / "Offer - Detail.txt"
        self.document.write_text("needle content", encoding="utf-8")
        self.manager = IndexManager(
            self.temp_path / "index.db",
            options=IndexOptions(ocr_enabled=False),
        )
        self.addCleanup(self.manager.close)

    def test_schema_identifiers_helpers_current_and_deprecated_entrypoint(self):
        with self.assertRaises(ValueError):
            self.manager._validate_identifier("bad-name", "table")
        self.assertFalse(self.manager._is_year_bucket(None))
        self.assertFalse(self.manager._is_year_bucket("20x6"))
        for filename, expected in (
            ("Project vom 2026.txt", "Project"),
            ("Project - Detail.txt", "Project"),
            ("Project, City.txt", "Project"),
            ("Plain.txt", "Plain"),
            (" - Detail.txt", "- Detail"),
            (", Name.txt", ", Name"),
        ):
            self.assertEqual(self.manager._derive_project_from_filename(filename), expected)
        self.assertFalse(self.manager.has_index_for_root(self.source))
        with self.assertWarns(DeprecationWarning):
            count = self.manager.index_directory(self.source)
        self.assertEqual(count, 1)
        self.assertTrue(self.manager.has_index_for_root(self.source.resolve()))
        self.assertTrue(self.manager.index_is_current(self.source.resolve()))
        self.manager.set_metadata("options_fingerprint", "wrong")
        self.assertFalse(self.manager.index_is_current(self.source.resolve()))
        with self.assertRaises(FileNotFoundError):
            self.manager.synchronize_directory(self.temp_path / "missing")
        with self.assertRaises(InterruptedError):
            self.manager.synchronize_directory(self.source, should_cancel=lambda: True)
        cancellation = Mock(side_effect=[False, False, False, False, True])
        with self.assertRaises(InterruptedError):
            self.manager.synchronize_directory(self.source, should_cancel=cancellation)
        with patch(
            "app.core.index_manager.os.walk",
            return_value=[(str(self.source), [], ["missing.txt"])],
        ):
            self.manager.synchronize_directory(self.source)

        disabled = IndexManager(self.temp_path / "disabled.db", content_enabled=False)
        self.addCleanup(disabled.close)
        self.assertFalse(disabled.content_index_needs_rebuild(self.source))
        disabled.synchronize_directory(self.source, full_rebuild=True)
        self.assertEqual(disabled._extract_document_with_status(self.document, "txt"), ("", "not_applicable", ""))
        no_types = IndexManager(
            self.temp_path / "no-types.db",
            options=IndexOptions(content_extensions=""),
        )
        self.addCleanup(no_types.close)
        self.assertFalse(no_types.content_index_needs_rebuild(self.source))
        read_only = IndexManager(self.manager.db_path, initialize=False)
        with read_only as opened:
            self.assertIs(opened, read_only)

    def test_search_detail_filter_and_content_helpers(self):
        self.manager.synchronize_directory(self.source)
        all_filters = SearchFilters(
            domain_folder="Service", year="2026", file_type=".txt"
        )
        sql, parameters = self.manager._metadata_filter_clause(all_filters)
        self.assertEqual(len(parameters), 3)
        self.assertIn("file_type", sql)
        self.assertEqual(self.manager.search_customers("Customer", limit=1)[0]["file_count"], 1)
        self.assertTrue(self.manager.search_customers("Customer"))
        self.assertEqual(self.manager.get_search_facets()["years"], ["2026"])
        self.assertEqual(self.manager.search_folders_page("!!!", SearchFilters()).total, 0)
        self.assertEqual(self.manager.search_folders("Customer", limit=1)[0]["folder_name"], "Customer, Berlin")
        folder_path = str(self.project.resolve())
        self.assertIsNone(self.manager.get_folder_search_entry("/missing", SearchFilters()))
        self.assertEqual(
            self.manager.get_folder_search_entry(folder_path, all_filters)["file_count"], 1
        )
        self.assertEqual(self.manager.get_folder_summary(folder_path)["file_count"], 1)
        orphan = str(self.project / "orphan")
        self.manager.conn.execute(
            "INSERT INTO folders(path,name,relative_path,parent_path,index_root) "
            "VALUES(?,?,?,?,?)",
            (orphan, "orphan", "orphan", "/missing-parent", str(self.source)),
        )
        self.assertEqual(self.manager.get_folder_details(folder_path)["file_count"], 1)
        self.assertEqual(self.manager.get_customer_details("Customer, Berlin")["file_count"], 1)
        self.assertEqual(self.manager.search_files("Offer", limit=1)[0]["filename"], self.document.name)
        self.assertEqual(
            self.manager.search_files_page("Offer", SearchFilters(), customer_name="Customer, Berlin").total,
            1,
        )
        self.assertTrue(self.manager.list_project_roots())
        self.assertIn("needle", self.manager.indexed_text_for_folder(folder_path, max_characters=6))
        self.assertEqual(self.manager.indexed_text_for_folder(folder_path, max_characters=0), "")
        self.assertEqual(
            self.manager.indexed_documents_for_folder(folder_path, max_documents=0)[0]["content"],
            "needle content",
        )
        self.assertEqual(self.manager._build_fts_query(""), "")
        self.assertEqual(self.manager._search_extracted_content("!!!", None), [])
        for sort in (SearchSort.DATE, SearchSort.ALPHABETICAL, SearchSort.RELEVANCE):
            self.manager._search_extracted_content(
                "needle", "Customer, Berlin", filters=SearchFilters(sort_order=sort)
            )
        self.assertEqual(len(self.manager._hash_file(self.document)), 64)
        self.assertEqual(self.manager._limit_text("x" * 3_000_000), "x" * 2_000_000)
        self.manager.conn.execute(
            "INSERT INTO file_content_fts(path,content) VALUES(?, '')",
            (str(self.project / "empty.txt"),),
        )
        self.assertIn("needle", self.manager.indexed_text_for_folder(folder_path))

        flat = self.source / "Service" / "2026" / "Flat.txt"
        flat.write_text("flat", encoding="utf-8")
        binary = self.project / "ignored.bin"
        binary.write_bytes(b"binary")
        self.manager.synchronize_directory(self.source)
        for number in range(250):
            (self.source / f"bulk-{number}.bin").touch()
        self.manager.synchronize_directory(self.source)

        large_manager = IndexManager(
            self.temp_path / "large.db",
            options=IndexOptions(max_file_size_mb=0, ocr_enabled=False),
        )
        self.addCleanup(large_manager.close)
        large_manager.synchronize_directory(self.source)
        status = large_manager.conn.execute(
            "SELECT content_status FROM files WHERE path=?", (str(self.document),)
        ).fetchone()[0]
        self.assertEqual(status, "skipped_large")
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("insert failed")
        self.manager._index_file(self.document, self.source, cursor)

    def test_ripgrep_parsing_targets_process_timeouts_and_search_guards(self):
        self.manager.synchronize_directory(self.source)
        event = {
            "type": "match",
            "data": {
                "path": {"text": str(self.document)},
                "lines": {"text": " match \n"}, "line_number": 4,
            },
        }
        parsed = self.manager._parse_rg_output(
            "bad\n" + json.dumps({"type": "begin"}) + "\n" +
            json.dumps({"type": "match", "data": {"path": {}, "lines": {}}}) + "\n" +
            json.dumps(event)
        )
        self.assertEqual(parsed[0]["line"], 4)
        self.assertTrue(self.manager._iter_text_search_targets("Customer, Berlin"))
        self.assertTrue(self.manager._iter_text_search_targets(None))

        process = Mock()
        process.communicate.return_value = ("output", "")
        with patch("app.core.index_manager.subprocess.Popen", return_value=process):
            self.assertEqual(self.manager._run_ripgrep(["rg"], None), "output")

        process = Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("rg", 1),
            subprocess.TimeoutExpired("rg", 1),
            ("", ""),
        ]
        with patch("app.core.index_manager.subprocess.Popen", return_value=process):
            self.assertEqual(self.manager._run_ripgrep(["rg"], lambda: True), "")
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

        process = Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("rg", 1), ("eventual", "")
        ]
        with (
            patch("app.core.index_manager.subprocess.Popen", return_value=process),
            patch("app.core.index_manager.time.monotonic", side_effect=[0, 1]),
        ):
            self.assertEqual(self.manager._run_ripgrep(["rg"], None), "eventual")

        process = Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("rg", 1), ("", "")
        ]
        with (
            patch("app.core.index_manager.subprocess.Popen", return_value=process),
            patch("app.core.index_manager.time.monotonic", side_effect=[0, 30]),
        ):
            self.assertEqual(self.manager._run_ripgrep(["rg"], None), "")
        process.kill.assert_called_once_with()

        with patch.object(self.manager, "_search_extracted_content", return_value=[{"path": "a"}]):
            self.assertEqual(self.manager.search_in_text("q", should_cancel=lambda: True), [{"path": "a"}])
            self.assertEqual(
                self.manager.search_in_text("q", filters=SearchFilters(year="2026")),
                [{"path": "a"}],
            )
        with patch.object(self.manager, "_search_extracted_content", side_effect=RuntimeError):
            self.assertEqual(self.manager.search_in_text("q"), [])
        duplicate = {"path": "same", "filename": "same", "line": 1}
        with (
            patch.object(self.manager, "_search_extracted_content", return_value=[]),
            patch.object(self.manager, "_iter_text_search_targets", return_value=[["root"]]),
            patch.object(self.manager, "_run_ripgrep", return_value="output"),
            patch.object(self.manager, "_parse_rg_output", return_value=[duplicate, duplicate]),
            patch("app.core.index_manager.RIPGREP_AVAILABLE", True),
            patch("app.core.index_manager.shutil.which", return_value="rg"),
        ):
            self.assertEqual(len(self.manager.search_in_text("q")), 1)
        with (
            patch.object(self.manager, "_search_extracted_content", return_value=[]),
            patch("app.core.index_manager.RIPGREP_AVAILABLE", False),
        ):
            self.assertEqual(self.manager.search_in_text("q"), [])
        with (
            patch.object(self.manager, "_search_extracted_content", return_value=[]),
            patch("app.core.index_manager.RIPGREP_AVAILABLE", True),
            patch("app.core.index_manager.shutil.which", return_value=None),
        ):
            self.assertEqual(self.manager.search_in_text("q"), [])
        with (
            patch.object(self.manager, "_search_extracted_content", return_value=[]),
            patch.object(self.manager, "_iter_text_search_targets", return_value=[]),
            patch("app.core.index_manager.RIPGREP_AVAILABLE", True),
            patch("app.core.index_manager.shutil.which", return_value="rg"),
        ):
            self.assertEqual(self.manager.search_in_text("q"), [])
        with (
            patch.object(self.manager, "_search_extracted_content", return_value=[]),
            patch.object(self.manager, "_iter_text_search_targets", return_value=[["root"]]),
            patch("app.core.index_manager.RIPGREP_AVAILABLE", True),
            patch("app.core.index_manager.shutil.which", return_value="rg"),
        ):
            self.assertEqual(
                self.manager.search_in_text("q", should_cancel=lambda: True), []
            )
        cancellation = Mock(side_effect=[False, True])
        with (
            patch.object(self.manager, "_search_extracted_content", return_value=[]),
            patch.object(self.manager, "_iter_text_search_targets", return_value=[["root"]]),
            patch("app.core.index_manager.RIPGREP_AVAILABLE", True),
            patch("app.core.index_manager.shutil.which", return_value="rg"),
        ):
            self.assertEqual(self.manager.search_in_text("q", should_cancel=cancellation), [])
        with (
            patch.object(self.manager, "_search_extracted_content", return_value=[]),
            patch.object(self.manager, "_iter_text_search_targets", return_value=[["root"]]),
            patch.object(self.manager, "_run_ripgrep", return_value=""),
            patch("app.core.index_manager.RIPGREP_AVAILABLE", True),
            patch("app.core.index_manager.shutil.which", return_value="rg"),
        ):
            self.assertEqual(self.manager.search_in_text("q"), [])
        page = self.manager.search_text_page("needle", SearchFilters(), page=2, page_size=1)
        self.assertEqual(page.page, 2)
        detached = IndexManager(self.temp_path / "detached.db")
        detached.close()
        detached.conn = None
        detached.close()

    def test_fuzzy_search_skips_unscored_and_disappearing_candidates(self):
        self.manager.synchronize_directory(self.source)
        with patch("app.core.fuzzy_search.fuzzy_record_score", return_value=None):
            page = self.manager.search_folders_page("Custmer", SearchFilters())
        self.assertEqual(page.total, 0)

        folder_path = str(self.project.resolve())

        def remove_candidate(*_arguments, **_keywords):
            self.manager.conn.execute("DELETE FROM folders WHERE path=?", (folder_path,))
            self.manager.conn.commit()
            return 1.0

        with patch(
            "app.core.fuzzy_search.fuzzy_record_score", side_effect=remove_candidate
        ):
            page = self.manager.search_folders_page("Custmer", SearchFilters())
        self.assertEqual(page.items, [])
