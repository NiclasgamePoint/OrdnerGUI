from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch

from app.core.catalog_index import CatalogIndexManager, CatalogStore
from app.core.config import IndexOptions
from app.core.index_layout import IndexLayout
from tests.base.test_case import PapaGuiTestCase


class CatalogIndexEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.layout = IndexLayout(self.temp_path / "index")
        self.store = CatalogStore(self.layout)

    def _build(self, name="source"):
        source = self.temp_path / name
        source.mkdir()
        (source / "file.txt").write_text("text", encoding="utf-8")
        build = self.store.create_build_path()
        with CatalogIndexManager(build) as manager:
            manager.synchronize_directory(source, full_rebuild=True)
        return source, build

    def test_current_contract_and_reconciliation_order_and_deletions(self):
        source, build = self._build()
        with CatalogIndexManager(build) as manager:
            self.assertTrue(manager.index_is_current(source))
            rows = manager.content_candidates()
            manager.options = IndexOptions(content_extensions="")
            self.assertFalse(manager.index_is_current(source))
            self.assertEqual(manager._refresh_content_contract(), 1)
            self.assertEqual(manager._refresh_content_contract(), 0)

            manager.content_candidates = Mock(return_value=rows)
            manager.get_metadata = Mock(return_value="generation")
            state = Mock()
            state.reconcile_document.return_value = ("old.db", True)
            state.finalize_generation.return_value = [
                {"document_key": "stale", "shard_name": "stale.db"}
            ]
            shards = Mock()
            manager.reconcile_content_state(
                state, shards, ["file"], newest_years_first=False,
            )
            self.assertEqual(shards.delete.call_count, 2)

    def test_seed_validation_restore_and_backup_edge_cases(self):
        empty = self.temp_path / "empty.db"
        with self.assertRaises(ValueError):
            self.store.validate(empty)
        empty.touch()
        with self.assertRaises(ValueError):
            self.store.validate(empty)

        source, build = self._build()
        seeded = self.store.create_build_path()
        self.store.seed_build(seeded, incremental=False)
        self.assertFalse(seeded.exists())
        self.store.activate(build)
        seeded.write_text("old", encoding="utf-8")
        self.store.seed_build(seeded, incremental=True)
        self.assertEqual(self.store.validate(seeded).file_count, 1)

        connection = sqlite3.connect(seeded)
        connection.execute("CREATE VIRTUAL TABLE file_content_fts USING fts5(content)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, "Dokumentinhalte"):
            self.store.validate(seeded)
        seeded.unlink()
        self.store.seed_build(seeded, incremental=True)
        connection = sqlite3.connect(seeded)
        connection.execute(
            "UPDATE index_metadata SET value='wrong' WHERE key='schema_version'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, "schema"):
            self.store.validate(seeded)

        with self.assertRaisesRegex(ValueError, "Ungültige"):
            self.store.create_restore_build(self.temp_path / "other.db")
        missing_backup = self.store.backup_paths()[0]
        with self.assertRaisesRegex(ValueError, "nicht mehr"):
            self.store.create_restore_build(missing_backup)

    def test_activation_rollbacks_and_restore_success(self):
        _source, first = self._build("first")
        self.store.activate(first)
        _source, second = self._build("second")
        real_replace = __import__("os").replace
        calls = 0

        def fail_build(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("locked")
            return real_replace(source, destination)

        with patch("app.core.catalog_index.os.replace", side_effect=fail_build):
            with self.assertRaises(OSError):
                self.store.activate(second)
        self.assertTrue(self.layout.catalog_path.exists())
        self.store.activate(second)

        backup = self.store.backup_paths()[0]
        restored = self.store.create_restore_build(backup)
        self.assertTrue(restored.exists())

        empty_layout = IndexLayout(self.temp_path / "empty-index")
        empty_store = CatalogStore(empty_layout)
        with patch("app.core.catalog_index.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                empty_store.activate(restored)

    def test_validate_integrity_failure_and_available_backup_skip(self):
        fake = Mock()
        fake.execute.return_value.fetchone.return_value = ("bad",)
        with (
            patch("app.core.catalog_index.sqlite3.connect", return_value=fake),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "stat", return_value=Mock(st_size=1)),
            self.assertRaisesRegex(ValueError, "Integritätsfehler"),
        ):
            self.store.validate(self.temp_path / "bad.db")
        self.assertEqual(self.store.available_backups(), [])
