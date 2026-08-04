from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.content_index import (
    ContentShard,
    ContentStateRepository,
    ContentTask,
    ShardRepository,
)
from app.core.index_layout import IndexLayout


class ContentIndexTests(unittest.TestCase):
    def test_state_queue_is_resumable_and_version_changes_are_requeued(self):
        with TemporaryDirectory() as directory:
            state = ContentStateRepository(Path(directory) / "state.db")
            state.reconcile_document(
                document_key="doc-1",
                path="/source/test.txt",
                source_version="v1",
                partition_year=2026,
                source_size=10,
                priority=10,
                catalog_generation="generation-1",
            )
            state.connection.commit()
            task = state.acquire_next()
            self.assertEqual(task.document_key, "doc-1")
            state.complete(
                task,
                shard_name="2026-001.db",
                content_status="success",
                extracted_characters=12,
            )
            self.assertTrue(state.progress().complete)

            state.reconcile_document(
                document_key="doc-1",
                path="/source/test.txt",
                source_version="v2",
                partition_year=2026,
                source_size=12,
                priority=10,
                catalog_generation="generation-2",
            )
            state.connection.commit()

            changed = state.acquire_next()
            self.assertIsNotNone(changed)
            self.assertEqual(changed.source_version, "v2")
            state.close()

    def test_contentless_fts_uses_compressed_text_and_replaces_old_version(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "2026-001.db"
            first = ContentTask("doc-1", "/a.txt", "v1", 2026, 1, 1)
            second = ContentTask("doc-1", "/a.txt", "v2", 2026, 1, 1)
            with ContentShard(path) as shard:
                shard.upsert(first, "Ein besonders seltener Prüfbegriff")
                self.assertEqual(len(shard.search("Prüfbegriff")), 1)
                self.assertIn("seltener", shard.get_text("doc-1"))

                shard.upsert(second, "Vollständig anderer Ersatztext")
                self.assertEqual(shard.search("Prüfbegriff"), [])
                results = shard.search("Ersatztext")
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].source_version, "v2")
                self.assertTrue(shard.integrity_check())

    def test_shards_are_partitioned_by_year_and_unassigned(self):
        with TemporaryDirectory() as directory:
            layout = IndexLayout(Path(directory) / "index")
            layout.ensure_directories()
            with ContentStateRepository(layout.content_state_path) as state:
                repository = ShardRepository(layout, state)
                self.assertEqual(repository.writable_shard(2026), "2026-001.db")
                self.assertEqual(
                    repository.writable_shard(None), "unassigned-001.db"
                )
                self.assertEqual(
                    set(repository.shard_names()),
                    {"2026-001.db", "unassigned-001.db"},
                )

    def test_year_shard_rolls_over_after_configured_size(self):
        with TemporaryDirectory() as directory:
            layout = IndexLayout(Path(directory) / "index")
            layout.ensure_directories()
            with ContentStateRepository(layout.content_state_path) as state:
                repository = ShardRepository(layout, state, target_bytes=4096)
                first_name = repository.writable_shard(2026)
                with ContentShard(layout.shard_path(first_name)):
                    pass
                second_name = repository.writable_shard(2026)
                self.assertEqual(first_name, "2026-001.db")
                self.assertEqual(second_name, "2026-002.db")

    def test_corrupt_state_is_isolated_and_recreated(self):
        with TemporaryDirectory() as directory:
            layout = IndexLayout(Path(directory) / "index")
            layout.ensure_directories()
            layout.content_state_path.write_bytes(b"keine SQLite-Datenbank")

            with ContentStateRepository.open_recoverable(layout) as state:
                self.assertEqual(state.progress().total_documents, 0)

            isolated = list(layout.corrupt_shard_dir.glob("state-*.db"))
            self.assertEqual(len(isolated), 1)
            self.assertEqual(isolated[0].read_bytes(), b"keine SQLite-Datenbank")

    def test_partition_change_clears_the_previous_shard_assignment(self):
        with TemporaryDirectory() as directory:
            state = ContentStateRepository(Path(directory) / "state.db")
            state.reconcile_document(
                document_key="doc-1",
                path="/source/test.txt",
                source_version="v1",
                partition_year=2025,
                source_size=10,
                priority=10,
                catalog_generation="generation-1",
            )
            state.connection.execute(
                "UPDATE documents SET shard_name='2025-001.db' WHERE document_key='doc-1'"
            )
            moved_from, changed = state.reconcile_document(
                document_key="doc-1",
                path="/source/test.txt",
                source_version="v2",
                partition_year=2026,
                source_size=10,
                priority=10,
                catalog_generation="generation-2",
            )
            row = state.connection.execute(
                "SELECT shard_name,partition_year FROM documents WHERE document_key='doc-1'"
            ).fetchone()
            self.assertTrue(changed)
            self.assertEqual(moved_from, "2025-001.db")
            self.assertEqual(row["shard_name"], "")
            self.assertEqual(row["partition_year"], 2026)
            state.close()


if __name__ == "__main__":
    unittest.main()
