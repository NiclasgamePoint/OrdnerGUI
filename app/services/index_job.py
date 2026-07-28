from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import time

from app.core.config import (
    load_customer_recognition_options,
    load_index_options,
)
from app.core.index_job_state import (
    activated_path,
    cancel_path,
    process_is_alive,
    read_owner,
    utc_now,
    write_state,
)
from app.core.index_manager import IndexManager
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
            validate_index(self.build_path)

            if changed_count == 0 and self.active_path.exists():
                customer_state = self._recognize_customers(self.build_path)
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
                customer_state = self._recognize_customers(self.active_path)
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
                    activate_index(self.active_path, self.build_path)
                    self.build_path = None
                    customer_state = self._recognize_customers(self.active_path)
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
            ).synchronize()
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
