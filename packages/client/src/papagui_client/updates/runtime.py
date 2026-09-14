"""Launch verified versions beside the installer, preserving application data."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import uuid

from papagui_client import __version__
from papagui_client.config import default_data_root

from .feed import ReleaseFeed, UpdateError, version
from .storage import FileLock, atomic_json, extract_bundle, idle, private_directory, snapshot


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"amd64", "x86_64"} else "unsupported"
    system = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform, "unsupported")
    return f"{system}-{arch}"


def executable(bundle: Path, role: str) -> Path:
    if role not in {"client", "tray"}:
        raise UpdateError("Unknown client product")
    if sys.platform == "darwin":
        label = "Client" if role == "client" else "Tray"
        return bundle / f"PapaGUI {label}.app/Contents/MacOS" / f"papagui-{role}"
    return bundle / (f"papagui-{role}" + (".exe" if os.name == "nt" else ""))


def read_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        raise UpdateError("Invalid local update state")
    if "version" in payload:
        version(payload["version"])
    return payload


def stage(root: Path, current: str, feed: ReleaseFeed) -> bool:
    lock = FileLock(root / "download.lock")
    if not lock.acquire(blocking=False):
        return False
    try:
        release = feed.latest()
        if release is None or version(release.version) <= version(current):
            return False
        rejected = read_state(root / "rejected.json")
        if rejected.get("version") == release.version:
            return False
        target = root / "versions" / release.version
        if target.exists():
            # The directory rename may have survived a crash before ready.json.
            # Only complete, previously verified bundles get this destination.
            if all(executable(target, role).is_file() for role in ("client", "tray")):
                atomic_json(root / "ready.json", {"version": release.version})
                return True
            return False
        asset = release.clients.get(platform_key())
        if asset is None:
            return False
        with TemporaryDirectory(prefix="download-", dir=root) as temporary:
            temporary = Path(temporary)
            archive, unpacked = temporary / "bundle.tar.gz", temporary / "bundle"
            feed.download(asset, archive)
            extract_bundle(archive, unpacked)
            for role in ("client", "tray"):
                if not executable(unpacked, role).is_file():
                    raise UpdateError("Update bundle is missing a product")
            target.parent.mkdir(exist_ok=True)
            os.replace(unpacked, target)
        atomic_json(root / "ready.json", {"version": release.version})
        return True
    finally:
        lock.close()


def activate(root: Path, data: Path, *, probe=subprocess.run) -> dict:
    """Caller holds activation.lock. Other processes keep their version alive."""
    active = read_state(root / "active.json")
    ready = read_state(root / "ready.json")
    current = max(active.get("version", __version__), __version__, key=version)
    if not ready or version(ready["version"]) <= version(current) or not idle(root):
        return active
    candidate = ready["version"]
    if read_state(root / "rejected.json").get("version") == candidate:
        return active
    bundle = root / "versions" / candidate
    with TemporaryDirectory(prefix="probe-", dir=root) as temporary:
        env = os.environ.copy()
        env.update(PAPAGUI_UPDATER_CHILD="1", PAPAGUI_CLIENT_DATA_ROOT=temporary, PAPAGUI_CLIENT_CONFIG_PATH=str(Path(temporary) / "config.json"), QT_QPA_PLATFORM="offscreen")
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        env["PAPAGUI_EXPECTED_VERSION"] = candidate
        env.pop("PAPAGUI_UPDATE_READY_FILE", None)
        for role in ("client", "tray"):
            result = probe([str(executable(bundle, role)), "--update-probe"], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                atomic_json(root / "rejected.json", {"version": candidate, "reason": "startup-probe"})
                raise UpdateError("New program failed its isolated startup check")
    backup = root / "backups" / (candidate + "-" + uuid.uuid4().hex)
    backup.mkdir(parents=True)
    snapshot(data, backup / "data")
    configured = os.getenv("PAPAGUI_CLIENT_CONFIG_PATH")
    if configured:
        config = Path(configured).resolve()
        if not config.is_relative_to(data.resolve()) and config.is_file():
            shutil.copy2(config, backup / "external-client-config.json")
    active = {"version": candidate, "previous": current, "backup": backup.name}
    atomic_json(root / "active.json", active)
    (root / "ready.json").unlink(missing_ok=True)
    return active


def signal_ready() -> None:
    target = os.environ.pop("PAPAGUI_UPDATE_READY_FILE", None)
    if target:
        atomic_json(Path(target), {"ready": True})


def probe_startup() -> int:
    """Native-library and data-initialization check using a disposable profile."""
    if os.getenv("PAPAGUI_EXPECTED_VERSION", __version__) != __version__:
        return 1
    from PySide6 import QtWidgets, QtPdf
    from papagui_client.composition import build_client

    assert QtWidgets.QApplication and QtPdf.QPdfDocument
    with TemporaryDirectory(prefix="papagui-update-probe-") as temporary:
        os.environ["PAPAGUI_CLIENT_DATA_ROOT"] = temporary
        os.environ["PAPAGUI_CLIENT_CONFIG_PATH"] = str(Path(temporary) / "client-config.json")
        build_client()
    return 0


def launch(role: str, arguments: list[str]) -> int | None:
    """Return None for source/test invocations; manage installed GUI processes."""
    if os.environ.pop("PAPAGUI_UPDATER_CHILD", "") == "1":
        return None
    if not getattr(sys, "frozen", False) or any(a in arguments for a in ("--help", "-h", "--update-probe")):
        return None
    data = Path(os.getenv("PAPAGUI_CLIENT_DATA_ROOT", str(default_data_root()))).resolve()
    root = data.parent / (data.name + "-updates")
    private_directory(root)
    lease_path = root / "sessions" / (uuid.uuid4().hex + ".lock")
    lease = FileLock(lease_path)
    with FileLock(root / "activation.lock"):
        try:
            active = activate(root, data) if os.getenv("PAPAGUI_AUTO_UPDATE", "1") != "0" else read_state(root / "active.json")
        except (OSError, ValueError, UpdateError, subprocess.SubprocessError):
            active = read_state(root / "active.json")
            atomic_json(root / "status.json", {"status": "update-deferred", "reason": "Activation check or backup failed; existing version retained"})
        selected = active.get("version", __version__)
        if version(selected) < version(__version__):
            selected = __version__  # A newer manually installed package wins.
            active = {}
        program = executable(root / "versions" / selected, role) if selected != __version__ else Path(sys.executable)
        if not program.is_file():
            raise UpdateError("Active program is missing; reinstall or restore the previous version")
        lease.acquire()
    stopped = threading.Event()

    def background():
        while not stopped.is_set():
            try:
                stage(root, selected, ReleaseFeed())
            except Exception:
                # Tokens and signed redirect URLs must never enter diagnostics.
                try:
                    atomic_json(root / "status.json", {"status": "check-failed", "reason": "Release unavailable or verification failed; installed version retained"})
                except OSError:
                    pass
            stopped.wait(6 * 60 * 60)

    if not any(a in arguments for a in ("--offline", "--sync-only")) and os.getenv("PAPAGUI_AUTO_UPDATE", "1") != "0":
        threading.Thread(target=background, daemon=True, name="release-check").start()
    ready_file = root / ("startup-" + uuid.uuid4().hex + ".json")
    env = os.environ.copy()
    env.update(PAPAGUI_UPDATER_CHILD="1", PAPAGUI_UPDATE_READY_FILE=str(ready_file))
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        result = subprocess.run([str(program), *arguments], env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
        if result and not ready_file.exists() and active.get("previous"):
            with FileLock(root / "activation.lock"):
                current = read_state(root / "active.json")
                if current.get("version") == selected:
                    atomic_json(root / "active.json", {"version": active["previous"]})
                    atomic_json(root / "rejected.json", {"version": selected, "reason": "startup-failed"})
            # Never overwrite possible user edits. The previous program shares
            # the same data epoch; the pre-update backup remains available.
        return result
    finally:
        stopped.set()
        lease.close()
        lease_path.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)


def try_launch(role: str, arguments: list[str]) -> int | None:
    try:
        return launch(role, arguments)
    except (OSError, ValueError, UpdateError, subprocess.SubprocessError):
        # The installer-provided executable remains usable offline if update
        # state is corrupt or its directory cannot be written.
        return None
