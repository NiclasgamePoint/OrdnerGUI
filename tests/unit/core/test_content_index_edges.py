from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.content_index import (
    ContentDocumentRepository,
    ContentShard,
    ContentStateRepository,
    ContentTask,
    ShardMaintenanceService,
    ShardRepository,
)
from app.core.index_layout import IndexLayout
from tests.base.test_case import PapaGuiTestCase


class ContentIndexEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.layout = IndexLayout(self.temp_path / "index")
        self.layout.ensure_directories()

    @staticmethod
    def _queue(state, key="doc", *, year=2026, generation="current"):
        state.reconcile_document(
            document_key=key,
            path=f"/source/{key}.txt",
            source_version="v1",
            partition_year=year,
            source_size=10,
            priority=5,
            catalog_generation=generation,
        )
        state.connection.commit()

    def test_schema_migration_recovery_and_acquire_rollback(self):
        valid_layout = IndexLayout(self.temp_path / "valid-index")
        valid_layout.ensure_directories()
        with ContentStateRepository.open_recoverable(valid_layout) as valid:
            self.assertEqual(valid.progress().total_documents, 0)
        connection = sqlite3.connect(self.layout.content_state_path)
        connection.execute("CREATE TABLE documents(document_key TEXT PRIMARY KEY)")
        connection.commit()
        connection.close()
        with self.assertRaises(sqlite3.OperationalError):
            ContentStateRepository(self.layout.content_state_path)

        repository = ContentStateRepository(self.temp_path / "state.db")
        repository.connection = Mock()
        repository.connection.execute.side_effect = [None, RuntimeError("broken")]
        with self.assertRaises(RuntimeError):
            repository.acquire_next()
        repository.connection.rollback.assert_called_once_with()

        legacy_path = self.temp_path / "legacy.db"
        legacy = sqlite3.connect(legacy_path)
        legacy.execute(
            "CREATE TABLE documents(document_key TEXT PRIMARY KEY,path TEXT NOT NULL,"
            "source_version TEXT NOT NULL,partition_year INTEGER,source_size INTEGER NOT NULL,"
            "priority INTEGER NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL,"
            "lease_until TEXT NOT NULL,shard_name TEXT NOT NULL,content_status TEXT NOT NULL,"
            "content_error TEXT NOT NULL,extracted_characters INTEGER NOT NULL,updated_at TEXT NOT NULL,"
            "catalog_generation TEXT NOT NULL)"
        )
        legacy.commit()
        legacy.close()
        with ContentStateRepository(legacy_path) as migrated:
            columns = {row[1] for row in migrated.connection.execute("PRAGMA table_info(documents)")}
        self.assertIn("error_category", columns)
        self.assertIn("last_parser", columns)

        class RecoverableRepository(ContentStateRepository):
            calls = 0

            def __init__(self, path):
                super().__init__(path)
                type(self).calls += 1
                if type(self).calls == 1:
                    real_connection = self.connection

                    def execute(sql, *arguments):
                        if sql == "PRAGMA integrity_check":
                            return SimpleNamespace(fetchone=lambda: ("corrupt",))
                        return real_connection.execute(sql, *arguments)

                    self.connection = Mock(wraps=real_connection)
                    self.connection.execute.side_effect = execute

        self.layout.content_state_path.unlink(missing_ok=True)
        with RecoverableRepository.open_recoverable(self.layout) as recovered:
            self.assertEqual(recovered.progress().total_documents, 0)

    def test_state_queue_filters_failures_leases_and_generation_cleanup(self):
        with ContentStateRepository(self.layout.content_state_path) as state:
            self._queue(state, "old", year=2024, generation="old")
            self._queue(state, "new", year=2026)
            self._queue(state, "low", year=None)
            state.connection.execute("UPDATE documents SET priority=99 WHERE document_key='low'")
            state.connection.commit()
            task = state.acquire_next(maximum_priority=10, newest_years_first=False)
            self.assertEqual(task.document_key, "old")
            state.fail(task, "retry", maximum_attempts=3, category="parse", last_parser="x")
            retry = state.acquire_next(maximum_priority=10)
            state.fail(retry, "final", maximum_attempts=1)
            self.assertIsNone(state.acquire_next(maximum_attempts=1, maximum_priority=0))

            active = state.acquire_next(maximum_priority=10)
            state.connection.execute(
                "UPDATE documents SET lease_until='2000-01-01',status='processing' WHERE document_key=?",
                (active.document_key,),
            )
            state.recover_expired_leases()
            self.assertIsNotNone(state.acquire_next(maximum_priority=10))
            stale = state.finalize_generation("current")
            self.assertEqual([row["document_key"] for row in stale], ["old"])

    def test_shard_empty_delete_search_excerpt_and_repository_paths(self):
        task = ContentTask("doc", "/a.txt", "v1", 2026, 1, 1)
        path = self.layout.shard_path("2026-001.db")
        with ContentShard(path) as shard:
            shard.upsert(ContentTask("empty", "/empty.txt", "v1", 2026, 1, 1), "")
            self.assertEqual(shard.search("!!!"), [])
            self.assertEqual(shard.get_text("missing"), "")
            shard.delete("missing")
            shard.upsert(task, "x" * 150 + " needle " + "y" * 150)
            self.assertTrue(shard.search("needle", limit=0)[0].excerpt.startswith("…"))
            shard.delete("doc")
            self.assertEqual(shard.free_page_ratio() >= 0, True)
            shard.optimize()
            shard.compact()

        with ContentStateRepository(self.layout.content_state_path) as state:
            self._queue(state)
            shards = ShardRepository(self.layout, state)
            self.assertEqual(shards.store(task, "content"), "2026-001.db")
            state.connection.execute(
                "UPDATE documents SET shard_name='2026-001.db' WHERE document_key='doc'"
            )
            state.connection.commit()
            self.assertEqual(shards.store(task, "replacement"), "2026-001.db")
            self.assertEqual(shards.shard_names({2026}), ["2026-001.db"])
            shards.delete("doc", "")
            shards.delete("doc", "missing.db")
            shards.delete("doc", "2026-001.db")
            large = ShardRepository(self.layout, state, target_bytes=10**9)
            self.assertEqual(large.writable_shard(2026), "2026-001.db")

    def _create_catalog(self, folder, rows):
        catalog = sqlite3.connect(self.layout.catalog_path)
        catalog.execute(
            "CREATE TABLE files(document_key TEXT,path TEXT,filename TEXT,file_type TEXT,"
            "source_version TEXT,project_root_path TEXT,folder_path TEXT,modified_date TEXT,"
            "content_eligible INTEGER)"
        )
        catalog.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)", rows)
        catalog.commit()
        catalog.close()

    def test_content_document_repository_complete_pending_and_mismatched_data(self):
        repository = ContentDocumentRepository(self.layout, self.layout.catalog_path)
        folder = str((self.temp_path / "customer").resolve())
        self.assertEqual(repository.documents_for_folder(folder), [])
        self.assertFalse(repository.has_pending_documents(folder))
        rows = [
            ("good", f"{folder}/good.txt", "good.txt", "TXT", "v1", folder, folder, "2", 1),
            ("good2", f"{folder}/good2.txt", "good2.txt", "TXT", "v1", folder, folder, "2", 1),
            ("empty", f"{folder}/empty.txt", "empty.txt", "TXT", "v1", folder, folder, "2", 1),
            ("wrong", f"{folder}/wrong.txt", "wrong.txt", "TXT", "v2", folder, folder, "1", 1),
        ]
        self._create_catalog(folder, rows)
        with ContentStateRepository(self.layout.content_state_path) as state:
            self._queue(state, "good")
            self._queue(state, "good2")
            self._queue(state, "empty")
            self._queue(state, "wrong")
            shards = ShardRepository(self.layout, state)
            for content in ("useful text", "second text", ""):
                task = state.acquire_next()
                shard_name = shards.store(task, content)
                state.complete(
                    task, shard_name=shard_name, content_status="success",
                    extracted_characters=len(content),
                )
            state.connection.execute(
                "UPDATE documents SET status='completed',source_version='other',shard_name=? WHERE document_key='wrong'",
                (shard_name,),
            )
            state.connection.commit()
        documents = repository.documents_for_folder(folder, max_characters_per_document=6)
        self.assertEqual(documents[0]["content"], "useful")
        self.assertEqual(len(documents), 2)
        self.assertFalse(repository.has_pending_documents(folder))
        with ContentStateRepository(self.layout.content_state_path) as state:
            state.connection.execute("UPDATE documents SET status='pending' WHERE document_key='wrong'")
            state.connection.commit()
        self.assertTrue(repository.has_pending_documents(folder))

    def test_document_repository_empty_keys_and_maintenance_branches(self):
        folder = str((self.temp_path / "empty").resolve())
        self._create_catalog(folder, [])
        with ContentStateRepository(self.layout.content_state_path):
            pass
        repository = ContentDocumentRepository(self.layout, self.layout.catalog_path)
        self.assertEqual(repository.documents_for_folder(folder), [])
        self.assertFalse(repository.has_pending_documents(folder))

        with ContentStateRepository(self.layout.content_state_path) as state:
            self._queue(state, "missing")
            state.connection.execute(
                "UPDATE documents SET shard_name='missing.db',status='completed' WHERE document_key='missing'"
            )
            state.connection.execute(
                "INSERT INTO shards(name,partition_year,sequence,status,updated_at) VALUES('missing.db',2026,1,'open','now')"
            )
            state.connection.commit()
            result = ShardMaintenanceService(self.layout, state).maintain()
            self.assertEqual(result["requeued"], 1)

            self._queue(state, "corrupt")
            corrupt = self.layout.shard_path("corrupt.db")
            corrupt.write_bytes(b"bad")
            state.connection.execute(
                "UPDATE documents SET shard_name='corrupt.db',status='completed' WHERE document_key='corrupt'"
            )
            state.connection.execute(
                "INSERT INTO shards(name,partition_year,sequence,status,updated_at) VALUES('corrupt.db',2026,2,'open','now')"
            )
            state.connection.commit()
            result = ShardMaintenanceService(self.layout, state).maintain()
            self.assertEqual(result["requeued"], 1)

            valid = self.layout.shard_path("valid.db")
            with ContentShard(valid):
                pass
            state.connection.execute(
                "INSERT INTO shards(name,partition_year,sequence,status,updated_at) VALUES('valid.db',2026,3,'open','now')"
            )
            state.connection.commit()
            with patch.object(ContentShard, "free_page_ratio", return_value=1.0):
                result = ShardMaintenanceService(self.layout, state).maintain()
            self.assertEqual(result["compacted"], 1)
            with patch.object(ContentShard, "free_page_ratio", return_value=0.0):
                result = ShardMaintenanceService(self.layout, state).maintain()
            self.assertEqual(result["optimized"], 1)

            duplicate = self.layout.corrupt_shard_dir / "valid.db"
            duplicate.write_bytes(b"old")
            with patch.object(ContentShard, "integrity_check", return_value=False):
                result = ShardMaintenanceService(self.layout, state).maintain()
            self.assertEqual(result["requeued"], 0)

            vanishing = self.layout.shard_path("vanishing.db")
            vanishing.write_bytes(b"placeholder")
            state.connection.execute(
                "INSERT INTO shards(name,partition_year,sequence,status,updated_at) "
                "VALUES('vanishing.db',2026,4,'open','now')"
            )
            state.connection.commit()

            def fail_after_remove(path):
                path.unlink()
                raise sqlite3.DatabaseError("gone")

            with patch("app.core.content_index.ContentShard", side_effect=fail_after_remove):
                ShardMaintenanceService(self.layout, state).maintain()
