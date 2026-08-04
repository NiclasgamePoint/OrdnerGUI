from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

from app.core.config import load_customer_recognition_options, load_index_options
from app.core.catalog_index import CatalogIndexManager, CatalogStore
from app.core.content_index import ContentStateRepository, ShardRepository
from app.core.index_job_state import (
    activated_path,
    cancel_path,
    process_is_alive,
    read_state,
    read_owner,
    utc_now,
    write_state,
)
from app.core.index_manager import IndexManager
from app.core.index_layout import IndexLayout
from app.core.index_store import (
    activate_index,
    create_build_path,
    seed_build_database,
    validate_index,
)
from app.core.logging_config import configure_logging
from app.core.process_support import suppress_windows_crash_dialogs
from app.services.customer_recognition import CustomerRecognitionService


logger = logging.getLogger(__name__)


class IndexJobRunner:
    """Build a staged index and survive independently of the GUI process."""

    def __init__(
        self,
        job_id: str,
        active_path: Path,
        source_path: Path,
        state_dir: Path,
        full_rebuild: bool,
        customer_database_path: Path,
    ):
        self.job_id = job_id
        self.active_path = active_path.resolve()
        self.source_path = source_path.resolve()
        self.state_dir = state_dir.resolve()
        self.full_rebuild = full_rebuild
        self.customer_database_path = customer_database_path.resolve()
        self.build_path: Path | None = None
        self.index_layout = (
            IndexLayout(self.active_path.parents[1])
            if self.active_path.name == "active.db"
            and self.active_path.parent.name == "catalog"
            else None
        )
        self._catalog_store = (
            CatalogStore(self.index_layout) if self.index_layout is not None else None
        )
        self._last_progress_write = 0.0
        self._last_progress_path = ""
        self._base_state = {
            "job_id": job_id,
            "pid": os.getpid(),
            "source": str(self.source_path),
            "active_path": str(self.active_path),
            "full_rebuild": full_rebuild,
            "customer_database_path": str(self.customer_database_path),
            "started_at": utc_now(),
        }

    def run(self) -> int:
        manager = None
        try:
            self._write(status="running", processed_count=0, current_path="")
            if self._catalog_store is not None:
                self.build_path = self._catalog_store.create_build_path()
                self._catalog_store.seed_build(
                    self.build_path, incremental=not self.full_rebuild
                )
                manager = CatalogIndexManager(
                    self.build_path, options=load_index_options()
                )
            else:
                self.build_path = create_build_path(self.active_path)
                seed_build_database(
                    self.active_path,
                    self.build_path,
                    incremental=not self.full_rebuild,
                )
                manager = IndexManager(self.build_path, options=load_index_options())
            indexed_count = manager.synchronize_directory(
                self.source_path,
                full_rebuild=self.full_rebuild,
                should_cancel=self._cancel_requested,
                progress_callback=self._progress,
            )
            changed_count = getattr(manager, "last_change_count", indexed_count)
            manager.close()
            manager = None
            if self._catalog_store is not None:
                self._catalog_store.validate(self.build_path)
            else:
                validate_index(self.build_path)

            if changed_count == 0 and self.active_path.exists():
                customer_state = self._recognize_customers(self.build_path)
                if self._catalog_store is not None:
                    self._reconcile_content_queue(self.active_path)
                    self._start_content_job()
                self.build_path.unlink(missing_ok=True)
                self.build_path = None
                self._write(
                    status="no_changes",
                    indexed_count=indexed_count,
                    changed_count=0,
                    **customer_state,
                )
                return 0

            self._write(
                status="ready",
                indexed_count=indexed_count,
                changed_count=changed_count,
                build_path=str(self.build_path),
            )
            return self._wait_for_activation(indexed_count, changed_count)
        except InterruptedError:
            self._cleanup_build()
            self._write(status="cancelled", error="")
            return 2
        except Exception as exc:
            logger.exception("Abgekoppelter Indexjob fehlgeschlagen")
            self._cleanup_build()
            self._write(status="error", error=str(exc))
            return 1
        finally:
            if manager is not None:
                manager.close()

    def _progress(self, processed_count: int, current_path: str):
        now = time.monotonic()
        if (
            current_path != self._last_progress_path
            or processed_count < 10
            or now - self._last_progress_write >= 0.4
        ):
            self._last_progress_write = now
            self._last_progress_path = current_path
            self._write(
                status="running",
                processed_count=processed_count,
                current_path=current_path,
            )

    def _cancel_requested(self) -> bool:
        return cancel_path(self.state_dir).exists()

    def _wait_for_activation(self, indexed_count: int, changed_count: int) -> int:
        owner_missing_since = None
        while True:
            if activated_path(self.state_dir).exists():
                customer_state = self._after_catalog_activation(start_content=False)
                self._write(
                    status="completed",
                    indexed_count=indexed_count,
                    changed_count=changed_count,
                    activated_by="gui",
                    **customer_state,
                )
                return 0
            if self._cancel_requested():
                self._cleanup_build()
                self._write(status="cancelled", error="")
                return 2
            owner_pid = read_owner(self.state_dir)
            if not process_is_alive(owner_pid):
                owner_missing_since = owner_missing_since or time.monotonic()
                # Give a newly launched GUI enough time to adopt the job before
                # replacing the database it is about to open (especially on Windows).
                if time.monotonic() - owner_missing_since >= 1.0:
                    self._activate_build()
                    self.build_path = None
                    customer_state = self._after_catalog_activation(start_content=True)
                    self._write(
                        status="completed",
                        indexed_count=indexed_count,
                        changed_count=changed_count,
                        activated_by="worker",
                        **customer_state,
                    )
                    return 0
            else:
                owner_missing_since = None
            time.sleep(0.25)

    def _recognize_customers(self, index_path: Path) -> dict:
        try:
            stats = CustomerRecognitionService(
                index_path,
                self.customer_database_path,
                load_customer_recognition_options(),
            ).synchronize(include_documents=self._catalog_store is None)
            return {
                "customers_detected": stats.detected,
                "customers_created": stats.created,
                "customers_assigned": stats.assigned,
                "customers_skipped": stats.skipped,
                "customer_cases_pending": stats.pending,
                "customer_sync_error": stats.error,
            }
        except Exception as error:
            logger.exception("Automatische Kundenerkennung fehlgeschlagen")
            return {
                "customers_detected": 0,
                "customers_created": 0,
                "customers_assigned": 0,
                "customers_skipped": 0,
                "customer_cases_pending": 0,
                "customer_sync_error": str(error),
            }

    def _activate_build(self):
        if self.build_path is None:
            raise RuntimeError("Es ist kein aktivierbarer Katalog vorhanden.")
        if self._catalog_store is not None:
            self._catalog_store.activate(self.build_path)
        else:
            activate_index(self.active_path, self.build_path)

    def _after_catalog_activation(self, *, start_content: bool) -> dict:
        if self._catalog_store is None:
            return self._recognize_customers(self.active_path)
        self._reconcile_content_queue(self.active_path)
        customer_state = self._recognize_customers(self.active_path)
        self._archive_legacy_index()
        if start_content:
            self._start_content_job()
        return customer_state

    def _reconcile_content_queue(self, catalog_path: Path):
        if self.index_layout is None:
            return
        manager = CatalogIndexManager(catalog_path, initialize=False)
        try:
            with ContentStateRepository.open_recoverable(self.index_layout) as state:
                shards = ShardRepository(self.index_layout, state)
                manager.reconcile_content_state(
                    state,
                    shards,
                    load_customer_recognition_options().preferred_patterns,
                )
        finally:
            manager.close()

    def _start_content_job(self):
        if self.index_layout is None:
            return
        state_dir = self.index_layout.jobs_dir / "content"
        state_dir.mkdir(parents=True, exist_ok=True)
        current = read_state(state_dir)
        if current.get("status") == "running" and process_is_alive(
            int(current.get("pid") or 0)
        ):
            return
        cancel_path(state_dir).unlink(missing_ok=True)
        command = [
            sys.executable,
            "-m",
            "app.services.content_index_job",
            "--index-root",
            str(self.index_layout.root),
            "--state-dir",
            str(state_dir),
            "--customers",
            str(self.customer_database_path),
        ]
        options = {
            "cwd": str(Path(__file__).resolve().parents[2]),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            options["start_new_session"] = True
        subprocess.Popen(command, **options)

    def _archive_legacy_index(self):
        if self.index_layout is None:
            return
        if not self.index_layout.catalog_path.exists():
            return
        legacy_index = self.index_layout.root.parent / "index.db"
        if not legacy_index.exists():
            return
        self.index_layout.legacy_dir.mkdir(parents=True, exist_ok=True)
        candidates = [
            legacy_index,
            *legacy_index.parent.glob("index.backup.*.db"),
            *legacy_index.parent.glob(".index.build-*.db*"),
            *legacy_index.parent.glob("index_job.*"),
        ]
        for source in candidates:
            if not source.exists():
                continue
            destination = self.index_layout.legacy_dir / source.name
            sequence = 1
            while destination.exists():
                destination = self.index_layout.legacy_dir / (
                    f"{source.stem}.{sequence}{source.suffix}"
                )
                sequence += 1
            os.replace(source, destination)

    def _cleanup_build(self):
        if self.build_path is not None:
            self.build_path.unlink(missing_ok=True)
            self.build_path = None

    def _write(self, **changes):
        state = {**self._base_state, **changes}
        write_state(self.state_dir, state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI background index job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--customers", type=Path, required=True)
    parser.add_argument("--full-rebuild", action="store_true")
    return parser


def main() -> int:
    suppress_windows_crash_dialogs()
    arguments = build_parser().parse_args()
    configure_logging()
    return IndexJobRunner(
        arguments.job_id,
        arguments.active,
        arguments.source,
        arguments.state_dir,
        arguments.full_rebuild,
        arguments.customers,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
