from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.core.catalog_index import CatalogIndexManager
from app.core.config import CustomerRecognitionOptions
from app.core.content_index import ContentStateRepository, ShardRepository
from app.core.content_search import ContentSearchService
from app.core.index_layout import IndexLayout
from app.core.search_models import SearchFilters
from app.services.content_index_job import ContentIndexJobRunner


class ContentIndexJobTests(unittest.TestCase):
    def test_detached_job_extracts_priorities_then_remaining_documents(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            project = source / "DEKRA" / "2026" / "Muster"
            archive = source / "Archiv" / "2010" / "Alt"
            project.mkdir(parents=True)
            archive.mkdir(parents=True)
            (project / "Angebot.txt").write_text(
                "Inhalt eines priorisierten Kundenprojekts", encoding="utf-8"
            )
            (archive / "Historie.txt").write_text(
                "Inhalt eines nachgelagerten Archivs", encoding="utf-8"
            )
            layout = IndexLayout(base / "index")
            layout.ensure_directories()
            with CatalogIndexManager(layout.catalog_path) as catalog:
                catalog.synchronize_directory(source, full_rebuild=True)
                with ContentStateRepository(layout.content_state_path) as state:
                    catalog.reconcile_content_state(
                        state, ShardRepository(layout, state), ["angebot"]
                    )

            runner = ContentIndexJobRunner(
                layout,
                layout.jobs_dir / "content",
                base / "customers.db",
                shard_target_bytes=1024 * 1024,
            )
            with patch(
                "app.services.content_index_job.load_customer_recognition_options",
                return_value=CustomerRecognitionOptions(enabled=False),
            ):
                result = runner.run()

            self.assertEqual(result, 0)
            page = ContentSearchService(layout, layout.catalog_path).search_page(
                "Inhalt", SearchFilters(), page_size=10
            )
            self.assertEqual(page.total, 2)
            self.assertTrue(page.coverage.complete)

    def test_job_rebuilds_a_missing_queue_from_the_active_catalog(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            document = source / "Dokument.txt"
            document.write_text("Wiederherstellbarer Inhalt", encoding="utf-8")
            layout = IndexLayout(base / "index")
            with CatalogIndexManager(layout.catalog_path) as catalog:
                catalog.synchronize_directory(source, full_rebuild=True)

            runner = ContentIndexJobRunner(
                layout,
                layout.jobs_dir / "content",
                base / "customers.db",
                shard_target_bytes=1024 * 1024,
            )
            with patch(
                "app.services.content_index_job.load_customer_recognition_options",
                return_value=CustomerRecognitionOptions(enabled=False),
            ):
                self.assertEqual(runner.run(), 0)

            page = ContentSearchService(layout, layout.catalog_path).search_page(
                "Wiederherstellbarer", SearchFilters(), page_size=10
            )
            self.assertEqual(page.total, 1)
            self.assertTrue(page.coverage.complete)


if __name__ == "__main__":
    unittest.main()
