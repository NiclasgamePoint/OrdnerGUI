"""Measure catalog construction using generated files, without customer data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time

from pyinstrument import Profiler

from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.domain.document_extraction import ExtractionResult
from papagui_server.domain.models import ServerSettings


class SyntheticExtractor:
    """Trivial reader so measurements expose catalog/cache costs, not OCR."""

    def fingerprint(self, _settings):
        return "synthetic-catalog-performance-v1"

    def extract_document(self, path, _settings, _cancelled):
        return ExtractionResult(text=path.read_text(encoding="utf-8"), status="ok")


def benchmark(documents: int, output: Path) -> list[dict]:
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with TemporaryDirectory(prefix="papagui-catalog-profile-") as directory:
        root = Path(directory)
        source = root / "source"
        for number in range(documents):
            project = source / "Synthetic" / "2026" / f"Project-{number // 25:04}" / "Documents"
            project.mkdir(parents=True, exist_ok=True)
            (project / f"document-{number:05}.txt").write_text(
                f"Synthetic document {number}. " + "Example content " * 500, encoding="utf-8"
            )
        for content in (False, True):
            mode = "content" if content else "metadata"
            indexer = SqliteCatalogIndexer(root / mode, SyntheticExtractor())
            settings = ServerSettings(content_indexing_enabled=content, ocr_enabled=False)
            for run in ("cold", "warm"):
                profiler = Profiler()
                started = time.perf_counter()
                profiler.start()
                try:
                    catalog = indexer.build(
                        source, source_id="synthetic", full_rebuild=False, settings=settings,
                        cancelled=lambda: False, progress=lambda *_: None,
                    )
                finally:
                    profiler.stop()
                elapsed = time.perf_counter() - started
                profiler.write_html(str(output.parent / f"{output.stem}-{mode}-{run}.html"))
                (output.parent / f"{output.stem}-{mode}-{run}.txt").write_text(
                    profiler.output_text(unicode=True, color=False), encoding="utf-8"
                )
                connection = sqlite3.connect(catalog)
                try:
                    assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == documents
                    assert connection.execute("SELECT COUNT(*) FROM file_content_fts").fetchone()[0] == (
                        documents if content else 0
                    )
                finally:
                    connection.close()
                result = {
                    "mode": mode, "run": run, "documents": documents,
                    "seconds": round(elapsed, 4),
                    "documents_per_second": round(documents / elapsed, 1),
                }
                results.append(result)
                print(json.dumps(result), flush=True)
    output.write_text(
        json.dumps({"scope": "Synthetic local inputs; not a production performance claim.", "runs": results}, indent=2) + "\n",
        encoding="utf-8",
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1500)
    parser.add_argument("--output", type=Path, default=Path("profiles/catalog.json"))
    arguments = parser.parse_args()
    if arguments.documents < 1:
        parser.error("documents must be positive")
    benchmark(arguments.documents, arguments.output)


if __name__ == "__main__":
    main()
