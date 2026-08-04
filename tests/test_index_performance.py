from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from app.core.config import IndexOptions
from app.core.content_index import ContentStateRepository
from app.core.index_layout import IndexLayout
from app.services.content_index_worker import ContentIndexWorker
from app.services.document_text_indexer import DocumentTextIndexer
from app.services.extraction_models import ExtractionResult
from app.services.index_resource_policy import GIB, IndexResourcePolicy, ResourceSnapshot
from app.services.index_tool_resolver import IndexToolResolver


class TrackingExtractor:
    def __init__(self):
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def extract(self, path: Path) -> ExtractionResult:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.03)
        with self.lock:
            self.active -= 1
        return ExtractionResult(text=path.name, status="success", file_type="txt")


class TimeoutExtractor:
    def extract(self, _path: Path) -> ExtractionResult:
        return ExtractionResult(status="timeout", error="too slow", parser="ocr")


class IndexPerformanceTests(unittest.TestCase):
    def test_tool_resolver_prefers_matching_packaged_poppler(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / IndexToolResolver.platform_tag() / "bin" / (
                "pdftotext.exe" if os.name == "nt" else "pdftotext"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"tool")
            with patch("app.services.index_tool_resolver.shutil.which") as which:
                resolved = IndexToolResolver(root).resolve("pdftotext")
            self.assertEqual(resolved, executable)
            which.assert_not_called()

    def test_resource_profiles_respect_cpu_ram_and_hard_cap(self):
        policy = IndexResourcePolicy()
        abundant = ResourceSnapshot(64, 64 * GIB, 48 * GIB)
        self.assertEqual(policy.worker_limit("gentle", abundant), 9)
        self.assertEqual(policy.worker_limit("balanced", abundant), 16)
        self.assertEqual(policy.worker_limit("fast", abundant), 20)
        constrained = ResourceSnapshot(32, 8 * GIB, 2 * GIB)
        self.assertEqual(policy.worker_limit("fast", constrained), 1)

    def test_documents_extract_in_parallel_but_writer_completes_each_once(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            layout = IndexLayout(base / "index")
            layout.ensure_directories()
            with ContentStateRepository(layout.content_state_path) as state:
                for number in range(6):
                    path = base / f"{number}.txt"
                    path.write_text(str(number), encoding="utf-8")
                    state.reconcile_document(
                        document_key=str(number), path=str(path), source_version="v1",
                        partition_year=2026, source_size=1, priority=1,
                        catalog_generation="g1",
                    )
                state.connection.commit()
            extractor = TrackingExtractor()
            activities = []
            result = ContentIndexWorker(
                layout, extractor, shard_target_bytes=1024**2, maximum_workers=3,
            ).run(activity_callback=lambda value: activities.append(value))
            self.assertEqual(result.processed, 6)
            self.assertGreaterEqual(extractor.maximum_active, 2)
            self.assertTrue(any(len(value) == 3 for value in activities))
            self.assertEqual(activities[-1], [])
            self.assertEqual(
                sorted(item["worker"] for item in next(
                    value for value in activities if len(value) == 3
                )),
                [1, 2, 3],
            )
            with ContentStateRepository(layout.content_state_path) as state:
                self.assertEqual(state.connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE status='completed'"
                ).fetchone()[0], 6)

    def test_timeout_is_not_retried_until_explicit_repair(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            layout = IndexLayout(base / "index")
            layout.ensure_directories()
            path = base / "slow.pdf"
            path.write_bytes(b"pdf")
            with ContentStateRepository(layout.content_state_path) as state:
                state.reconcile_document(
                    document_key="slow", path=str(path), source_version="v1",
                    partition_year=2026, source_size=3, priority=1,
                    catalog_generation="g1",
                )
                state.connection.commit()
            result = ContentIndexWorker(
                layout, TimeoutExtractor(), shard_target_bytes=1024**2,
                maximum_workers=2,
            ).run()
            self.assertEqual(result.failed, 1)
            with ContentStateRepository(layout.content_state_path) as state:
                row = state.connection.execute(
                    "SELECT status,attempts,error_category FROM documents"
                ).fetchone()
                self.assertEqual(tuple(row), ("failed", 1, "timeout"))

    def test_pdf_uses_pdftotext_before_fallback(self):
        indexer = DocumentTextIndexer(IndexOptions(ocr_enabled=False))
        with (
            patch.object(indexer.tools, "resolve", side_effect=[None, Path("pdftotext")]),
            patch.object(indexer, "_run", return_value=subprocess.CompletedProcess([], 0, "Text from Poppler", "")) as run,
        ):
            result = indexer._pdf_result(Path("doc.pdf"))
        self.assertEqual(result.parser, "pdftotext")
        self.assertEqual(result.text, "Text from Poppler")
        self.assertEqual(run.call_count, 1)

    def test_pdf_falls_back_to_isolated_pypdf_and_honors_timeout(self):
        indexer = DocumentTextIndexer(IndexOptions(
            ocr_enabled=False, pdf_text_timeout_seconds=45,
        ))
        completed = [
            subprocess.CompletedProcess([], 1, "", "poppler failed"),
            subprocess.CompletedProcess([], 0, "Fallback text", ""),
        ]
        with (
            patch.object(
                indexer.tools, "resolve",
                side_effect=lambda name: Path(name) if name == "pdftotext" else None,
            ),
            patch.object(indexer, "_run", side_effect=completed) as run,
        ):
            result = indexer._pdf_result(Path("doc.pdf"))
        self.assertEqual(result.parser, "PyPDF2")
        self.assertEqual(result.text, "Fallback text")
        self.assertEqual(run.call_count, 2)
        self.assertLessEqual(run.call_args_list[0].kwargs["timeout"], 45)
        self.assertLessEqual(run.call_args_list[1].kwargs["timeout"], 45)

    def test_document_log_contains_performance_fields_without_content(self):
        task_path = "/documents/private.pdf"
        from app.core.content_index import ContentTask
        task = ContentTask("key", task_path, "v1", 2026, 1, 1)
        result = ExtractionResult(
            text="SECRET CONTENT", status="success", parser="pdftotext",
            file_type="pdf", source_size=100, page_count=2,
            duration_seconds=1.5, parser_seconds=1.0,
            ocr_seconds=0.5, ocr_pages=2,
        )
        with self.assertLogs("app.services.content_index_worker", level="INFO") as logs:
            ContentIndexWorker._log_result(task, result)
        payload = json.loads(logs.output[0].split(":", 2)[2])
        self.assertEqual(payload["type"], "pdf")
        self.assertEqual(payload["bytes"], 100)
        self.assertEqual(payload["pages"], 2)
        self.assertEqual(payload["parser"], "pdftotext")
        self.assertEqual(payload["ocr_s"], 0.5)
        self.assertNotIn("SECRET CONTENT", logs.output[0])

    def test_ocr_runs_second_stage_only_below_threshold(self):
        options = IndexOptions(
            ocr_max_pages=5, ocr_extended_max_pages=25,
            ocr_extension_threshold=500,
        )
        indexer = DocumentTextIndexer(options)
        with (
            patch.object(indexer.tools, "resolve", side_effect=[Path("pdftoppm"), Path("tesseract")]),
            patch.object(indexer, "_ocr_range", side_effect=[["short"], ["extended"]]) as ranges,
        ):
            text, _pages, _count = indexer._ocr_pdf(Path("scan.pdf"))
        self.assertEqual(text, "short\nextended")
        self.assertEqual(ranges.call_count, 2)
        self.assertEqual(ranges.call_args_list[0].args[2:4], (1, 5))
        self.assertEqual(ranges.call_args_list[1].args[2:4], (6, 25))

    def test_ocr_stops_after_first_stage_when_text_is_sufficient(self):
        indexer = DocumentTextIndexer(IndexOptions(ocr_extension_threshold=500))
        with (
            patch.object(indexer.tools, "resolve", side_effect=[Path("pdftoppm"), Path("tesseract")]),
            patch.object(indexer, "_ocr_range", return_value=["x" * 500]) as ranges,
        ):
            text, pages, _count = indexer._ocr_pdf(Path("scan.pdf"))
        self.assertEqual(len(text), 500)
        self.assertEqual(pages, 1)
        self.assertEqual(ranges.call_count, 1)


if __name__ == "__main__":
    unittest.main()
