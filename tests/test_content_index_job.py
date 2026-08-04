from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.core.catalog_index import CatalogIndexManager
from app.core.config import CustomerRecognitionOptions
from app.core.content_index import ContentProgress, ContentStateRepository, ShardRepository
from app.core.index_job_state import read_state
from app.core.content_search import ContentSearchService
from app.core.index_layout import IndexLayout
from app.core.search_models import SearchFilters
from app.services.content_index_job import ContentIndexJobRunner
from app.gui.workers.content_job_controller import ContentJobController


class ContentIndexJobTests(unittest.TestCase):
    def test_progress_updates_preserve_identity_for_process_monitoring(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            layout = IndexLayout(base / "index")
            state_dir = layout.jobs_dir / "content"
            runner = ContentIndexJobRunner(layout, state_dir, base / "customers.db")
            runner._job_state = {
                "job_id": "job-123",
                "status": "running",
                "started_at": "2026-08-04T20:00:00+02:00",
                "pid": 4242,
            }
            runner._write()
            runner._progress(ContentProgress(10, 2, 8, 0, 1000, 200), "/a.pdf")
            runner._progress(ContentProgress(10, 3, 7, 0, 1000, 300), "/b.pdf")

            state = read_state(state_dir)
            self.assertEqual(state["job_id"], "job-123")
            self.assertEqual(state["pid"], 4242)
            self.assertEqual(state["started_at"], "2026-08-04T20:00:00+02:00")
            self.assertEqual(state["completed_documents"], 3)
            self.assertEqual(state["current_path"], "/b.pdf")

            controller = ContentJobController(layout, base / "customers.db")
            observed = []
            controller.progress.connect(observed.append)
            with patch(
                "app.gui.workers.content_job_controller.process_is_alive",
                return_value=True,
            ) as alive:
                controller.poll()
            alive.assert_called_with(4242)
            self.assertEqual(observed[-1]["status"], "running")
            self.assertNotIn("error", observed[-1])
            controller._timer.stop()

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
            job_state = read_state(layout.jobs_dir / "content")
            self.assertTrue(job_state["job_id"])
            self.assertGreater(job_state["pid"], 0)
            self.assertTrue(job_state["started_at"])
            self.assertEqual(job_state["status"], "completed")
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
