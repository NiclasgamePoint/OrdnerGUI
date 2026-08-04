from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.config import BASE_DIR
from app.core.index_job_state import (
    cancel_path,
    pause_path,
    process_is_alive,
    read_state,
    write_state,
)
from app.core.index_layout import IndexLayout
from app.core.logging_config import CONTENT_PROCESS_LOG_FILE, open_content_process_log


class ContentJobController(QObject):
    """Start or adopt the independently resumable content worker."""

    progress = Signal(object)
    finished = Signal(object)

    def __init__(
        self,
        layout: IndexLayout,
        customer_database_path: Path,
        parent=None,
    ):
        super().__init__(parent)
        self.layout = layout
        self.customer_database_path = customer_database_path
        self.state_dir = layout.jobs_dir / "content"
        self._last_update = ""
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.poll)

    def start_or_adopt(self) -> bool:
        if pause_path(self.state_dir).exists():
            return False
        current = read_state(self.state_dir)
        if current.get("status") == "running" and process_is_alive(
            int(current.get("pid") or 0)
        ):
            self._timer.start()
            return True
        if not self.layout.catalog_path.exists():
            return False
        cancel_path(self.state_dir).unlink(missing_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-u",
            "-m",
            "app.services.content_index_job",
            "--index-root",
            str(self.layout.root),
            "--state-dir",
            str(self.state_dir),
            "--customers",
            str(self.customer_database_path),
        ]
        options = {
            "cwd": str(BASE_DIR),
            "stdin": subprocess.DEVNULL,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            options["start_new_session"] = True
        process_log = open_content_process_log()
        options["stdout"] = process_log
        try:
            subprocess.Popen(command, **options)
        finally:
            process_log.close()
        self._timer.start()
        return True

    def poll(self):
        state = read_state(self.state_dir)
        if state.get("status") == "running" and not process_is_alive(
            int(state.get("pid") or 0)
        ):
            state = dict(state)
            state["status"] = "error"
            state["error"] = (
                "Der Dateiindexprozess wurde unerwartet beendet. "
                f"Protokoll: {CONTENT_PROCESS_LOG_FILE}"
            )
            write_state(self.state_dir, state)
        updated = str(state.get("updated_at") or "")
        if updated and updated != self._last_update:
            self._last_update = updated
            self.progress.emit(state)
        if state.get("status") in {"completed", "cancelled", "error"}:
            self._timer.stop()
            self.finished.emit(state)

    def is_active(self) -> bool:
        state = read_state(self.state_dir)
        return state.get("status") == "running" and process_is_alive(
            int(state.get("pid") or 0)
        )

    def cancel(self):
        state = read_state(self.state_dir)
        if state.get("status") == "running":
            self.state_dir.mkdir(parents=True, exist_ok=True)
            cancel_path(self.state_dir).touch()

    def pause(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        pause_path(self.state_dir).touch()
        self.cancel()

    def resume(self) -> bool:
        pause_path(self.state_dir).unlink(missing_ok=True)
        return self.start_or_adopt()
