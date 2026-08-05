from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core import config, index_job_state
from app.core.folder_structure import FolderStructureClassifier
from app.core.fuzzy_search import SearchField, _token_similarity, fuzzy_record_score
from app.core.index_store import (
    activate_index,
    available_backups,
    backup_paths,
    create_build_path,
    create_restore_build,
    seed_build_database,
    validate_index,
)
from tests.base.test_case import PapaGuiTestCase


def create_index(path: Path, *, integrity_schema: bool = True, built_at: str = "2026-01-01T12:00:00"):
    connection = sqlite3.connect(path)
    if integrity_schema:
        connection.executescript(
            "CREATE TABLE index_metadata(key TEXT, value TEXT);"
            "CREATE TABLE files(path TEXT);"
            "CREATE VIRTUAL TABLE file_content_fts USING fts5(text);"
        )
        connection.executemany(
            "INSERT INTO index_metadata VALUES(?,?)",
            [("built_at", built_at), ("index_root", "/source")],
        )
        connection.execute("INSERT INTO files VALUES('/file')")
    else:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    connection.close()


class PlatformAndStoreEdgeTests(PapaGuiTestCase):
    def test_config_sources_save_failures_and_recognition_round_trip(self):
        self.assertEqual(config.get_default_index_source(), config.BAUVORHABEN_DIR)
        settings = Mock()
        settings.value.return_value = ""
        with patch("app.core.config.QSettings", return_value=settings):
            self.assertEqual(config.get_configured_index_source(), config.BAUVORHABEN_DIR)
            self.assertFalse(config.has_configured_index_source())
            config.save_index_source(Path("/source"))
        settings.setValue.assert_called()
        settings.sync.assert_called()
        settings.value.return_value = "~/data"
        with patch("app.core.config.QSettings", return_value=settings):
            self.assertEqual(config.get_configured_index_source(), Path("~/data").expanduser())

        settings.status.return_value = SimpleNamespace(value=1)
        settings.fileName.return_value = "settings.ini"
        with patch("app.core.config.QSettings", return_value=settings), self.assertRaises(OSError):
            config.save_index_options(config.IndexOptions())

        memory = {}
        settings = Mock()
        settings.value.side_effect = lambda key, default=None: memory.get(key, default)
        settings.setValue.side_effect = lambda key, value: memory.__setitem__(key, value)
        with patch("app.core.config.QSettings", return_value=settings):
            options = config.CustomerRecognitionOptions(frequent_value_threshold=1)
            config.save_customer_recognition_options(options)
            loaded = config.load_customer_recognition_options()
        self.assertEqual(loaded.frequent_value_threshold, 2)

    def test_folder_and_fuzzy_edge_cases(self):
        classifier = FolderStructureClassifier()
        self.assertIsNone(classifier.classify("/outside", "/root"))
        self.assertIsNone(classifier.classify("/root/service/2026/, City", "/root"))
        self.assertEqual(_token_similarity("a", "b"), 0)
        self.assertGreater(_token_similarity("abc", "abcdef"), 0.8)
        self.assertGreater(_token_similarity("abc", "xabcx"), 0.8)
        self.assertEqual(_token_similarity("abc", "xyz"), 0)
        self.assertIsNone(fuzzy_record_score("", [SearchField("x")]))
        self.assertIsNone(fuzzy_record_score("x", []))
        self.assertIsNone(fuzzy_record_score("nomatch", [SearchField("different")]))
        self.assertGreater(fuzzy_record_score("exact", [SearchField("exact")]), 1)
        self.assertGreater(fuzzy_record_score("foo bar", [SearchField("x foo bar y")]), 0.8)
        self.assertIsNotNone(fuzzy_record_score("fo ba", [SearchField("foo bar")]))

    def test_index_seed_validate_restore_and_backup_listing(self):
        active = self.temp_path / "active.db"
        build = create_build_path(active)
        build.write_text("stale", encoding="utf-8")
        seed_build_database(active, build, incremental=True)
        self.assertFalse(build.exists())
        create_index(active)
        seed_build_database(active, build, incremental=True)
        self.assertEqual(validate_index(build)["file_count"], "1")
        empty = self.temp_path / "empty.db"
        empty.touch()
        with self.assertRaises(ValueError):
            validate_index(empty)
        malformed = self.temp_path / "malformed.db"
        create_index(malformed, integrity_schema=False)
        with self.assertRaises(sqlite3.OperationalError):
            validate_index(malformed)
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = ("corrupt",)
        with patch("app.core.index_store.sqlite3.connect", return_value=connection), self.assertRaisesRegex(ValueError, "Integritätsfehler"):
            validate_index(active)

        backups = backup_paths(active)
        create_index(backups[0], built_at="invalid")
        backups[1].write_text("invalid", encoding="utf-8")
        entries = available_backups(active)
        self.assertEqual(len(entries), 1)
        self.assertIn("1 Dateien", entries[0]["label"])
        with self.assertRaises(ValueError):
            create_restore_build(active, self.temp_path / "other.db")
        restored = create_restore_build(active, backups[0])
        self.assertTrue(restored.exists())

    def test_activation_rotates_and_restores_previous_database_on_failure(self):
        active = self.temp_path / "active.db"
        create_index(active)
        backups = backup_paths(active)
        for backup in backups:
            create_index(backup)
        build = self.temp_path / "build.db"
        create_index(build)
        activate_index(active, build)
        self.assertTrue(active.exists())
        self.assertTrue(backups[-1].exists())

        failed_build = self.temp_path / "failed.db"
        create_index(failed_build)
        original_replace = os.replace

        def fail_build(source, destination):
            if Path(source) == failed_build:
                raise OSError("replace failed")
            return original_replace(source, destination)

        with patch("app.core.index_store.os.replace", side_effect=fail_build), self.assertRaises(OSError):
            activate_index(active, failed_build)
        self.assertTrue(active.exists())
        absent_active = self.temp_path / "absent.db"
        second_build = self.temp_path / "second-build.db"
        create_index(second_build)
        with patch("app.core.index_store.os.replace", side_effect=OSError("failed")), self.assertRaises(OSError):
            activate_index(absent_active, second_build)

    def test_job_state_io_process_checks_and_control_files(self):
        state_dir = self.temp_path / "state"
        self.assertEqual(index_job_state.read_state(state_dir), {})
        state_dir.mkdir()
        index_job_state.state_path(state_dir).write_text("bad json", encoding="utf-8")
        self.assertEqual(index_job_state.read_state(state_dir), {})
        index_job_state.write_state(state_dir, {"status": "running"})
        self.assertEqual(index_job_state.read_state(state_dir)["status"], "running")
        self.assertEqual(index_job_state.pause_path(state_dir).name, "index_job.paused")
        with (
            patch("app.core.index_job_state.STATE_REPLACE_ATTEMPTS", 1),
            patch("app.core.index_job_state.os.replace", side_effect=PermissionError),
            self.assertRaises(PermissionError),
        ):
            index_job_state.write_state(state_dir, {"status": "failed"})
        with patch("app.core.index_job_state.STATE_REPLACE_ATTEMPTS", 0):
            index_job_state.write_state(state_dir, {"status": "skipped"})
        with (
            patch("app.core.index_job_state.STATE_REPLACE_ATTEMPTS", 2),
            patch(
                "app.core.index_job_state.os.replace",
                side_effect=[PermissionError, None],
            ),
            patch("app.core.index_job_state.time.sleep") as sleep,
        ):
            index_job_state.write_state(state_dir, {"status": "retried"})
        sleep.assert_called_once()

        index_job_state.write_owner(state_dir, 123)
        self.assertEqual(index_job_state.read_owner(state_dir), 123)
        index_job_state.release_owner(state_dir, 999)
        self.assertTrue(index_job_state.owner_path(state_dir).exists())
        index_job_state.release_owner(state_dir, 123)
        self.assertFalse(index_job_state.owner_path(state_dir).exists())
        index_job_state.owner_path(state_dir).write_text("bad", encoding="ascii")
        self.assertEqual(index_job_state.read_owner(state_dir), 0)

        self.assertFalse(index_job_state.process_is_alive(0))
        self.assertTrue(index_job_state.process_is_alive(os.getpid()))
        with patch("app.core.index_job_state.os.name", "posix"), patch("app.core.index_job_state.os.kill") as kill:
            self.assertTrue(index_job_state.process_is_alive(999))
        kill.assert_called_once_with(999, 0)
        for error, expected in ((ProcessLookupError(), False), (PermissionError(), True), (OSError(), False)):
            with patch("app.core.index_job_state.os.name", "posix"), patch("app.core.index_job_state.os.kill", side_effect=error):
                self.assertEqual(index_job_state.process_is_alive(999), expected)
        with patch("app.core.index_job_state.os.name", "nt"), patch.object(index_job_state, "_windows_process_is_alive", return_value=True):
            self.assertTrue(index_job_state.process_is_alive(999))

        index_job_state.cancel_path(state_dir).touch()
        index_job_state.activated_path(state_dir).touch()
        index_job_state.clear_control_files(state_dir)
        self.assertFalse(index_job_state.cancel_path(state_dir).exists())
        self.assertFalse(index_job_state.activated_path(state_dir).exists())

    def test_windows_process_probe_closes_handles_for_active_exited_and_missing(self):
        class Function:
            def __init__(self, result=None):
                self.result = result
                self.argtypes = None
                self.restype = None

            def __call__(self, *args):
                return self.result

        open_process = Function(0)
        exit_process = Function(True)
        close_handle = Function(True)
        kernel = SimpleNamespace(
            OpenProcess=open_process,
            GetExitCodeProcess=exit_process,
            CloseHandle=close_handle,
        )
        fake_ctypes = SimpleNamespace(
            WinDLL=Mock(return_value=kernel),
            POINTER=lambda value: value,
            byref=lambda value: value,
        )
        fake_wintypes = SimpleNamespace(DWORD=lambda: SimpleNamespace(value=259), BOOL=bool, HANDLE=int)
        fake_ctypes.wintypes = fake_wintypes
        with patch.dict(sys.modules, {"ctypes": fake_ctypes, "ctypes.wintypes": fake_wintypes}):
            self.assertFalse(index_job_state._windows_process_is_alive(4))
            open_process.result = 5
            self.assertTrue(index_job_state._windows_process_is_alive(4))
            exit_process.result = False
            self.assertFalse(index_job_state._windows_process_is_alive(4))
