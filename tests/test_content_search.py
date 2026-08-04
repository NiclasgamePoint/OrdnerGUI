from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from app.core.catalog_index import CatalogIndexManager
from app.core.content_index import ContentStateRepository, ShardRepository
from app.core.content_search import ContentSearchService
from app.core.index_layout import IndexLayout
from app.core.search_models import SearchFilters, SearchSort


class ContentSearchTests(unittest.TestCase):
    def test_progressive_search_merges_shards_and_rejects_stale_versions(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            first_folder = source / "DEKRA" / "2025" / "Alpha"
            second_folder = source / "DEKRA" / "2026" / "Beta"
            first_folder.mkdir(parents=True)
            second_folder.mkdir(parents=True)
            first_path = first_folder / "A.txt"
            second_path = second_folder / "B.txt"
            first_path.write_text("Gemeinsamer Suchbegriff Alpha", encoding="utf-8")
            second_path.write_text("Gemeinsamer Suchbegriff Beta", encoding="utf-8")
            layout = IndexLayout(base / "index")
            layout.ensure_directories()
            with CatalogIndexManager(layout.catalog_path) as catalog:
                catalog.synchronize_directory(source, full_rebuild=True)
                rows = catalog.conn.execute(
                    "SELECT document_key,path,source_version,partition_year,file_size "
                    "FROM files ORDER BY path"
                ).fetchall()

            with ContentStateRepository(layout.content_state_path) as state:
                shards = ShardRepository(layout, state)
                for row in rows:
                    state.reconcile_document(
                        document_key=row["document_key"],
                        path=row["path"],
                        source_version=row["source_version"],
                        partition_year=row["partition_year"],
                        source_size=row["file_size"],
                        priority=1,
                        catalog_generation="one",
                    )
                    state.connection.commit()
                    task = state.acquire_next()
                    content = Path(task.path).read_text(encoding="utf-8")
                    shard_name = shards.store(task, content)
                    state.complete(
                        task,
                        shard_name=shard_name,
                        content_status="success",
                        extracted_characters=len(content),
                    )

            service = ContentSearchService(layout, layout.catalog_path)
            page = service.search_page(
                "Suchbegriff",
                SearchFilters(sort_order=SearchSort.ALPHABETICAL),
                page_size=10,
            )
            self.assertEqual(page.total, 2)
            self.assertTrue(page.coverage.complete)
            self.assertEqual([item["filename"] for item in page.items], ["A.txt", "B.txt"])

            filtered = service.search_page(
                "Suchbegriff", SearchFilters(year="2026"), page_size=10
            )
            self.assertEqual(filtered.total, 1)
            self.assertEqual(filtered.items[0]["filename"], "B.txt")

            connection = sqlite3.connect(layout.catalog_path)
            connection.execute(
                "UPDATE files SET source_version='new' WHERE filename='A.txt'"
            )
            connection.commit()
            connection.close()
            current = service.search_page("Suchbegriff", SearchFilters(), page_size=10)
            self.assertEqual(current.total, 1)


if __name__ == "__main__":
    unittest.main()
