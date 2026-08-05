from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.content_index import ContentShard, ContentStateRepository
from app.core.index_layout import IndexLayout
from app.services.content_index_worker import ContentIndexWorker


class FakeExtractor:
    def extract(self, path: Path) -> tuple[str, str, str]:
        return path.read_text(encoding="utf-8"), "success", ""


class ContentIndexWorkerTests(unittest.TestCase):
    def test_worker_processes_queue_and_can_resume_after_cancellation(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            layout = IndexLayout(base / "index")
            layout.ensure_directories()
            documents = []
            with ContentStateRepository(layout.content_state_path) as state:
                for number in range(2):
                    path = base / f"{number}.txt"
                    path.write_text(f"Inhalt Nummer {number}", encoding="utf-8")
                    documents.append(path)
                    state.reconcile_document(
                        document_key=f"doc-{number}",
                        path=str(path),
                        source_version="v1",
                        partition_year=2026,
                        source_size=path.stat().st_size,
                        priority=number,
                        catalog_generation="one",
                    )
                state.connection.commit()

            callbacks = 0

            def cancel_after_first_progress(*_args):
                nonlocal callbacks
                callbacks += 1

            worker = ContentIndexWorker(
                layout, FakeExtractor(), shard_target_bytes=1024 * 1024
            )
            first = worker.run(
                should_cancel=lambda: callbacks >= 2,
                progress_callback=cancel_after_first_progress,
            )
            self.assertTrue(first.cancelled)
            self.assertEqual(first.processed, 1)

            second = worker.run()
            self.assertFalse(second.cancelled)
            self.assertTrue(second.progress.complete)
            self.assertEqual(second.processed, 1)
            with ContentShard(layout.shard_path("2026-001.db")) as shard:
                self.assertEqual(len(shard.search("Inhalt")), 2)


if __name__ == "__main__":
    unittest.main()
