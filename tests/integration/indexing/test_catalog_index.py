from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.catalog_index import CatalogIndexManager, CatalogStore
from app.core.content_index import ContentStateRepository, ShardRepository
from app.core.index_layout import IndexLayout


class CatalogIndexTests(unittest.TestCase):
    def test_catalog_scan_never_creates_fts_or_reads_document_content(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            project = source / "DEKRA" / "2026" / "Muster, Berlin"
            project.mkdir(parents=True)
            document = project / "Angebot.txt"
            document.write_text("Nicht Teil des Katalogs", encoding="utf-8")
            layout = IndexLayout(base / "data" / "index")
            store = CatalogStore(layout)
            build = store.create_build_path()
            with CatalogIndexManager(build) as manager:
                manager.synchronize_directory(source, full_rebuild=True)
                row = manager.conn.execute(
                    "SELECT document_key, source_version, content_eligible "
                    "FROM files WHERE path=?",
                    (str(document.resolve()),),
                ).fetchone()
                self.assertEqual(row["content_eligible"], 1)
                tables = {
                    item[0]
                    for item in manager.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("file_content_fts", tables)
                columns = {
                    item[1] for item in manager.conn.execute("PRAGMA table_info(files)")
                }
                self.assertTrue({
                    "document_key", "source_version", "content_eligible"
                }.issubset(columns))
                self.assertTrue({
                    "content_hash", "content_status", "content_error",
                    "full_text_indexed",
                }.isdisjoint(columns))
            validation = store.validate(build)
            self.assertEqual(validation.file_count, 1)

    def test_catalog_activation_rotates_small_catalog_backups(self):
        with TemporaryDirectory() as directory:
            layout = IndexLayout(Path(directory) / "index")
            store = CatalogStore(layout)
            for generation in range(5):
                source = Path(directory) / f"source-{generation}"
                source.mkdir()
                (source / "file.txt").write_text(str(generation), encoding="utf-8")
                build = store.create_build_path()
                with CatalogIndexManager(build) as manager:
                    manager.synchronize_directory(source, full_rebuild=True)
                store.activate(build)
            self.assertTrue(layout.catalog_path.exists())
            self.assertEqual(len(store.available_backups()), 3)

    def test_catalog_reconciles_content_queue_with_priorities(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            project = source / "DEKRA" / "2026" / "Muster"
            project.mkdir(parents=True)
            (project / "Angebot.txt").write_text("Text", encoding="utf-8")
            (project / "Sonstiges.txt").write_text("Text", encoding="utf-8")
            layout = IndexLayout(base / "index")
            layout.ensure_directories()
            with CatalogIndexManager(layout.catalog_path) as catalog:
                catalog.synchronize_directory(source, full_rebuild=True)
                with ContentStateRepository(layout.content_state_path) as state:
                    shards = ShardRepository(layout, state)
                    catalog.reconcile_content_state(state, shards, ["angebot"])
                    priorities = dict(state.connection.execute(
                        "SELECT path,priority FROM documents"
                    ))
                    self.assertEqual(priorities[str((project / "Angebot.txt").resolve())], 0)
                    self.assertEqual(priorities[str((project / "Sonstiges.txt").resolve())], 10)
                    self.assertEqual(state.progress().total_documents, 2)


if __name__ == "__main__":
    unittest.main()
