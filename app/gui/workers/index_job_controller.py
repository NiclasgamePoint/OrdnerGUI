from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.config import BASE_DIR, DATA_DIR
from app.core.index_job_state import (
    ACTIVE_STATUSES,
    activated_path,
    cancel_path,
    clear_control_files,
    process_is_alive,
    read_state,
    release_owner,
    write_owner,
    write_state,
)


class IndexJobController(QObject):
    """Start, adopt and observe a detached index subprocess."""

    progress = Signal(int, str)
    ready = Signal(object)
    finished = Signal(object)

    def __init__(self, active_path: Path, state_dir: Path = DATA_DIR, parent=None):
        super().__init__(parent)
        self.active_path = active_path.resolve()
        self.state_dir = state_dir.resolve()
        self._job_id = ""
        self._ready_emitted = False
        self._terminal_emitted = False
        self._last_progress = (-1, "")
        self._process: subprocess.Popen | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.poll)

    def adopt_running_job(self) -> bool:
        state = read_state(self.state_dir)
        if state.get("status") not in ACTIVE_STATUSES:
            return False
        if not process_is_alive(int(state.get("pid") or 0)):
            state["status"] = "error"
            state["error"] = "Der Hintergrundprozess wurde unerwartet beendet."
            write_state(self.state_dir, state)
            return False
        self._job_id = str(state.get("job_id") or "")
        self._ready_emitted = False
        self._terminal_emitted = False
        write_owner(self.state_dir, os.getpid())
        self._timer.start()
        return True

    def start(self, source: Path, full_rebuild: bool) -> bool:
        if self.is_active():
            return False
        clear_control_files(self.state_dir)
        write_owner(self.state_dir, os.getpid())
        self._job_id = uuid.uuid4().hex
        self._ready_emitted = False
        self._terminal_emitted = False
        self._last_progress = (-1, "")
        command = [
            sys.executable,
            "-m",
            "app.services.index_job",
            "--job-id",
            self._job_id,
            "--active",
            str(self.active_path),
            "--source",
            str(source.resolve()),
            "--state-dir",
            str(self.state_dir),
        ]
        if full_rebuild:
            command.append("--full-rebuild")
        popen_options = {
            "cwd": str(BASE_DIR),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            popen_options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            popen_options["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **popen_options)
        except Exception:
            release_owner(self.state_dir, os.getpid())
            raise
        self._process = process
        existing = read_state(self.state_dir)
        if existing.get("job_id") != self._job_id:
            write_state(self.state_dir, {
                "job_id": self._job_id,
                "pid": process.pid,
                "status": "starting",
                "source": str(source.resolve()),
                "active_path": str(self.active_path),
                "full_rebuild": full_rebuild,
                "processed_count": 0,
                "current_path": "",
            })
        self._timer.start()
        return True

    def poll(self):
        state = read_state(self.state_dir)
        if not state or str(state.get("job_id") or "") != self._job_id:
            return
        status = str(state.get("status") or "")
        if status in {"starting", "running"}:
            progress = (
                int(state.get("processed_count") or 0),
                str(state.get("current_path") or ""),
            )
            if progress != self._last_progress:
                self._last_progress = progress
                self.progress.emit(*progress)
            if not process_is_alive(int(state.get("pid") or 0)):
                state["status"] = "error"
                state["error"] = "Der Hintergrundprozess wurde unerwartet beendet."
                write_state(self.state_dir, state)
        elif status == "ready" and not self._ready_emitted:
            self._ready_emitted = True
            self.ready.emit(state)
        elif status in {"completed", "no_changes", "cancelled", "error"}:
            if not self._terminal_emitted:
                self._terminal_emitted = True
                self._timer.stop()
                if self._process is not None:
                    try:
                        self._process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                self.finished.emit(state)

    def acknowledge_activation(self):
        activated_path(self.state_dir).touch()

    def cancel(self):
        if self.is_active():
            cancel_path(self.state_dir).touch()

    def is_active(self) -> bool:
        state = read_state(self.state_dir)
        return (
            state.get("status") in ACTIVE_STATUSES
            and process_is_alive(int(state.get("pid") or 0))
        )

    def current_state(self) -> dict:
        return read_state(self.state_dir)

    def release_owner(self):
        release_owner(self.state_dir, os.getpid())
        self._timer.stop()
