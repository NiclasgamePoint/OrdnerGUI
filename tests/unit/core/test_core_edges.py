from __future__ import annotations

import logging
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.content_index import ContentStateRepository
from app.core.index_diagnostics import IndexDiagnosticsService
from app.core.index_layout import IndexLayout
from app.core.logging_config import configure_logging, open_content_process_log
from app.core.process_support import suppress_windows_crash_dialogs
from app.core.search_models import (
    RecentCustomerHistory,
    SearchFilters,
    SearchHistory,
    SearchPage,
    SearchPreferences,
    SearchSort,
)
from tests.base.test_case import PapaGuiTestCase


class MemorySettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)


class CoreEdgeTests(PapaGuiTestCase):
    def test_search_models_cover_invalid_persistence_and_boundaries(self):
        filters = SearchFilters()
        self.assertFalse(filters.active)
        self.assertTrue(SearchFilters(year="2026").active)
        self.assertEqual(SearchPage([], 0, 3, 10).page_count, 1)
        self.assertEqual(SearchPage([], 21, 1, 10).page_count, 3)

        preferences = SearchPreferences()
        preferences.settings = MemorySettings({preferences.SORT_KEY: "invalid", preferences.SUBFOLDERS_KEY: "YES"})
        self.assertEqual(preferences.load(), (SearchSort.RELEVANCE, True))
        preferences.save(SearchSort.DATE, False)
        self.assertEqual(preferences.load(), (SearchSort.DATE, False))
        preferences.save("alphabetical", True)
        self.assertEqual(preferences.load(), (SearchSort.ALPHABETICAL, True))
        preferences.save("invalid", False)
        self.assertEqual(preferences.load(), (SearchSort.RELEVANCE, False))

        history = SearchHistory(maximum=2)
        history.settings = MemorySettings({history.KEY: "First"})
        self.assertEqual(history.entries(), ["First"])
        self.assertEqual(history.add("  second "), ["second", "First"])
        self.assertEqual(history.add("SECOND"), ["SECOND", "First"])
        self.assertEqual(history.add(" "), ["SECOND", "First"])
        history.clear()
        self.assertEqual(history.entries(), [])

        recent = RecentCustomerHistory(maximum=2)
        recent.settings = MemorySettings({recent.KEY: "4"})
        self.assertEqual(recent.ids(), [4])
        recent.settings = MemorySettings({recent.KEY: ["bad", 0, "1", 1, None, 2, 3]})
        self.assertEqual(recent.ids(), [1, 2])
        self.assertEqual(recent.remember(["bad", -1]), [1, 2])
        self.assertEqual(recent.remember([3, "3", 2]), [3, 2])
        recent.clear()
        self.assertEqual(recent.ids(), [])

    def test_layout_creates_directories_and_rejects_unsafe_shards(self):
        layout = IndexLayout(self.temp_path / "index")
        layout.ensure_directories()
        for path in (
            layout.catalog_dir, layout.catalog_build_dir, layout.catalog_backup_dir,
            layout.content_dir, layout.shard_dir, layout.corrupt_shard_dir,
            layout.jobs_dir, layout.legacy_dir,
        ):
            self.assertTrue(path.is_dir())
        self.assertEqual(layout.shard_path("2026-1.db"), layout.shard_dir / "2026-1.db")
        for invalid in ("", "../bad.db", "bad.txt"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                layout.shard_path(invalid)

    def test_logging_is_idempotent_and_opens_content_stream(self):
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        for handler in original_handlers:
            root.removeHandler(handler)
        self.addCleanup(lambda: [root.addHandler(handler) for handler in original_handlers])
        log_path = self.temp_path / "logs" / "app.log"
        self.assertEqual(configure_logging(log_path), log_path)
        count = len(root.handlers)
        self.assertEqual(configure_logging(log_path), log_path)
        self.assertEqual(len(root.handlers), count)
        for handler in list(root.handlers):
            if getattr(handler, "_papagui_handler", False):
                handler.close()
                root.removeHandler(handler)

        stream_path = self.temp_path / "content.log"
        with patch("app.core.logging_config.CONTENT_PROCESS_LOG_FILE", stream_path):
            with open_content_process_log() as stream:
                stream.write("ok\n")
        self.assertEqual(stream_path.read_text(encoding="utf-8"), "ok\n")

    def test_windows_crash_dialog_suppression_handles_success_and_errors(self):
        kernel = SimpleNamespace(SetErrorMode=Mock())
        ctypes = SimpleNamespace(windll=SimpleNamespace(kernel32=kernel))
        with patch("app.core.process_support.os.name", "nt"), patch.dict(sys.modules, {"ctypes": ctypes}):
            suppress_windows_crash_dialogs()
        kernel.SetErrorMode.assert_called_once_with(0x0001 | 0x0002 | 0x8000)
        with patch("app.core.process_support.os.name", "nt"), patch.dict(sys.modules, {"ctypes": SimpleNamespace()}):
            suppress_windows_crash_dialogs()

    def test_diagnostics_handles_missing_and_legacy_catalog(self):
        service = IndexDiagnosticsService()
        missing = service.inspect(self.temp_path / "missing.db")
        self.assertEqual(missing.integrity, "fehlt")

        plain_path = self.temp_path / "plain.db"
        plain = sqlite3.connect(plain_path)
        plain.executescript(
            "CREATE TABLE index_metadata(key TEXT, value TEXT);"
            "CREATE TABLE files(path TEXT);CREATE TABLE folders(path TEXT);"
        )
        plain.commit()
        plain.close()
        self.assertEqual(service.inspect(plain_path).content_count, 0)

        catalog_path = self.temp_path / "catalog.db"
        connection = sqlite3.connect(catalog_path)
        connection.executescript(
            "CREATE TABLE index_metadata(key TEXT, value TEXT);"
            "CREATE TABLE files(path TEXT, content_status TEXT, content_error TEXT);"
            "CREATE TABLE folders(path TEXT);"
            "CREATE VIRTUAL TABLE file_content_fts USING fts5(text);"
            "INSERT INTO index_metadata VALUES('index_root','/source');"
            "INSERT INTO files VALUES('/bad','error','broken');"
            "INSERT INTO folders VALUES('/folder');"
            "INSERT INTO file_content_fts VALUES('text');"
        )
        connection.commit()
        connection.close()
        result = service.inspect(catalog_path)
        self.assertEqual(result.integrity, "ok")
        self.assertEqual(result.content_count, 1)
        self.assertEqual(result.status_counts, {"error": 1})
        self.assertEqual(result.errors, [{"path": "/bad", "error": "broken"}])

        layout = IndexLayout(self.temp_path / "index")
        layout.ensure_directories()
        with ContentStateRepository(layout.content_state_path) as state:
            state.reconcile_document(
                document_key="doc", path="/document", source_version="v1",
                partition_year=2026, source_size=4, priority=1,
                catalog_generation="one",
            )
            state.connection.execute(
                "UPDATE documents SET status='completed',content_status='error',"
                "content_error='failed' WHERE document_key='doc'"
            )
            state.connection.commit()
        (layout.shard_dir / "one.db").write_bytes(b"data")
        (layout.shard_dir / "nested.db").mkdir()
        result = service.inspect(catalog_path, layout.content_state_path)
        self.assertEqual(result.content_count, 1)
        self.assertEqual(result.status_counts, {"error": 1})
        self.assertGreater(result.content_database_size, 4)
