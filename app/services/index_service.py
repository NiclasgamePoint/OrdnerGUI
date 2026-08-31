"""Headless catalog and content indexing service for container deployments."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import threading
import uuid

from app.core.index_job_state import utc_now, write_state
from app.core.index_layout import IndexLayout
from app.core.logging_config import configure_logging
from app.services.content_index_job import ContentIndexJobRunner
from app.services.index_job import IndexJobRunner
from app.services.index_api import IndexApiServer
from app.services.index_distribution import IndexGenerationPublisher


logger = logging.getLogger(__name__)


class IndexService:
    """Run complete index generations without depending on a GUI process."""

    def __init__(
        self,
        source_path: Path,
        data_path: Path,
        interval_seconds: float = 86_400,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Das Indexintervall muss größer als null sein.")
        self.source_path = source_path.resolve()
        self.data_path = data_path.resolve()
        self.interval_seconds = interval_seconds
        self.layout = IndexLayout(self.data_path / "index")
        self.customer_database_path = self.data_path / "customers.db"
        self.catalog_state_dir = self.layout.jobs_dir / "catalog"
        self.content_state_dir = self.layout.jobs_dir / "content"
        self.service_state_dir = self.layout.jobs_dir / "service"
        self.publisher = IndexGenerationPublisher(self.data_path)
        self._stopped = threading.Event()
        self._state: dict[str, object] = {}

    def stop(self) -> None:
        """Request a graceful stop between durable indexing phases."""
        self._stopped.set()

    def run_once(self, *, full_rebuild: bool = False) -> int:
        """Build/refresh the catalog, enrich customers, then index contents."""
        self._validate_mounts()
        self.layout.ensure_directories()
        effective_rebuild = full_rebuild or not self.layout.catalog_path.exists()
        job_id = uuid.uuid4().hex
        self._write_state(
            status="catalog",
            job_id=job_id,
            source=str(self.source_path),
            full_rebuild=effective_rebuild,
            started_at=utc_now(),
        )
        catalog_result = IndexJobRunner(
            job_id,
            self.layout.catalog_path,
            self.source_path,
            self.catalog_state_dir,
            effective_rebuild,
            self.customer_database_path,
            launch_content_job=False,
        ).run()
        if catalog_result != 0:
            self._write_state(status="error", phase="catalog", exit_code=catalog_result)
            return catalog_result
        if self._stopped.is_set():
            self._write_state(status="stopped", phase="catalog")
            return 2

        self._write_state(status="content", phase="content")
        content_result = ContentIndexJobRunner(
            self.layout,
            self.content_state_dir,
            self.customer_database_path,
        ).run()
        if content_result == 0:
            self._write_state(status="publishing", phase="publishing")
            published = self.publisher.publish()
            self._write_state(generation=published.generation)
        final_status = "completed" if content_result == 0 else "error"
        self._write_state(
            status=final_status,
            phase="content",
            exit_code=content_result,
            completed_at=utc_now(),
        )
        return content_result

    def serve(self, *, full_rebuild: bool = False) -> int:
        """Run immediately and then refresh at a fixed interval until stopped."""
        first_run = True
        while not self._stopped.is_set():
            result = self.run_once(full_rebuild=full_rebuild and first_run)
            first_run = False
            if result not in {0, 2}:
                logger.error("Indexdienst-Lauf fehlgeschlagen (Exit-Code %s)", result)
            if self._stopped.wait(self.interval_seconds):
                break
        self._write_state(status="stopped", completed_at=utc_now())
        return 0

    def _validate_mounts(self) -> None:
        if not self.source_path.is_dir():
            raise ValueError(
                f"Die eingehängte Datenquelle ist nicht erreichbar: {self.source_path}"
            )
        if self.data_path.exists() and not self.data_path.is_dir():
            raise ValueError(
                f"Das eingehängte Ausgabeverzeichnis ist ungültig: {self.data_path}"
            )
        self.data_path.mkdir(parents=True, exist_ok=True)

    def _write_state(self, **values: object) -> None:
        self.service_state_dir.mkdir(parents=True, exist_ok=True)
        self._state.update(values)
        write_state(self.service_state_dir, self._state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI headless index service")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--full-rebuild", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=86_400)
    parser.add_argument("--api-host", default="0.0.0.0")
    parser.add_argument("--api-port", type=int, default=8765)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    configure_logging()
    service = IndexService(
        arguments.source,
        arguments.data,
        interval_seconds=arguments.interval_seconds,
    )
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop_signal, lambda *_args: service.stop())
    if arguments.once:
        return service.run_once(full_rebuild=arguments.full_rebuild)
    api = IndexApiServer(
        arguments.data,
        host=arguments.api_host,
        port=arguments.api_port,
        token=os.getenv("PAPAGUI_API_TOKEN", ""),
    )
    api.start()
    try:
        return service.serve(full_rebuild=arguments.full_rebuild)
    finally:
        api.stop()


if __name__ == "__main__":
    raise SystemExit(main())
