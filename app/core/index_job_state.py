from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any


STATE_FILENAME = "index_job.json"
OWNER_FILENAME = "index_job.owner"
CANCEL_FILENAME = "index_job.cancel"
ACTIVATED_FILENAME = "index_job.activated"
ACTIVE_STATUSES = {"starting", "running", "ready"}
TERMINAL_STATUSES = {"completed", "no_changes", "cancelled", "error"}


def state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILENAME


def owner_path(state_dir: Path) -> Path:
    return state_dir / OWNER_FILENAME


def cancel_path(state_dir: Path) -> Path:
    return state_dir / CANCEL_FILENAME


def activated_path(state_dir: Path) -> Path:
    return state_dir / ACTIVATED_FILENAME


def utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def read_state(state_dir: Path) -> dict[str, Any]:
    path = state_path(state_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state_dir: Path, state: dict[str, Any]):
    state_dir.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_at"] = utc_now()
    path = state_path(state_dir)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def write_owner(state_dir: Path, process_id: int):
    state_dir.mkdir(parents=True, exist_ok=True)
    owner_path(state_dir).write_text(str(process_id), encoding="ascii")


def read_owner(state_dir: Path) -> int:
    try:
        return int(owner_path(state_dir).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def release_owner(state_dir: Path, process_id: int):
    if read_owner(state_dir) == process_id:
        owner_path(state_dir).unlink(missing_ok=True)


def process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if process_id == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_is_alive(process_id)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_is_alive(process_id: int) -> bool:
    """Check a PID without sending a signal on Windows."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        process_id,
    )
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
            exit_code.value == still_active
        )
    finally:
        kernel32.CloseHandle(handle)


def clear_control_files(state_dir: Path):
    cancel_path(state_dir).unlink(missing_ok=True)
    activated_path(state_dir).unlink(missing_ok=True)
