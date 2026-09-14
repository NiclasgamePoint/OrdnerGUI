"""Private update staging, process leases and recoverable application snapshots."""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import time
import uuid

from .feed import UpdateError


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise UpdateError("Update directory must not be a symlink")
    if os.name == "nt":
        # Replace explicit rules too. Set-Acl can request an unavailable audit
        # privilege; the .NET method writes only the modified access section.
        script = """
$ErrorActionPreference = 'Stop'
$path = $env:PAPAGUI_PRIVATE_DIRECTORY
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
    $acl.RemoveAccessRuleSpecific($rule)
}
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $identity, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
$acl.AddAccessRule($rule)
[System.IO.Directory]::SetAccessControl($path, $acl)
"""
        env = os.environ.copy()
        env.pop("PSModulePath", None)
        env["PAPAGUI_PRIVATE_DIRECTORY"] = str(path)
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        path.chmod(0o700)


def atomic_json(path: Path, payload) -> None:
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class FileLock:
    """OS-owned lock: a terminated process never leaves a permanent lock."""

    def __init__(self, path: Path):
        self.path, self.stream = path, None

    def acquire(self, *, blocking=True) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if not blocking:
                            raise
                        time.sleep(0.1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            stream.close()
            if blocking:
                raise
            return False
        self.stream = stream
        return True

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.close()


def idle(root: Path) -> bool:
    """Call while holding the activation lock; new sessions use the same lock."""
    for path in (root / "sessions").glob("*.lock"):
        lock = FileLock(path)
        if not lock.acquire(blocking=False):
            return False
        lock.close()
        path.unlink(missing_ok=True)
    return True


@contextmanager
def session(root: Path):
    path = root / "sessions" / (uuid.uuid4().hex + ".lock")
    with FileLock(path):
        yield
    path.unlink(missing_ok=True)


def extract_bundle(archive: Path, target: Path) -> None:
    """Extract only bounded files/directories and in-bundle symlinks (macOS)."""
    target.mkdir()
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 30_000 or sum(m.size for m in members) > 4_000_000_000:
            raise UpdateError("Update bundle exceeds extraction limits")
        for member in members:
            if member.islnk() or not (member.isfile() or member.isdir() or member.issym()):
                raise UpdateError("Special archive members are forbidden")
            if "\\" in member.name or ":" in member.name:
                raise UpdateError("Unsafe archive path")
            destination = (target / member.name).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise UpdateError("Archive path escapes staging directory")
            if member.issym():
                resolved = (destination.parent / member.linkname).resolve()
                if not resolved.is_relative_to(target.resolve()):
                    raise UpdateError("Archive symlink escapes staging directory")
        source.extractall(target, members=members, filter="data")


def snapshot(source: Path, target: Path) -> None:
    """Copy a quiescent application directory; never follow user symlinks."""
    if source.resolve() == target.resolve() or target.resolve().is_relative_to(source.resolve()):
        raise UpdateError("Backup destination overlaps application data")
    if source.is_symlink():
        raise UpdateError("Automatic updates require a real data directory")
    if source.exists():
        shutil.copytree(source, target, symlinks=True)
        preserve_ownership(source, target)
        for database in target.rglob("*.db"):
            if database.is_symlink():
                continue
            # Connections are closed before activation. Verify the copied WAL
            # with its database, without changing the original data directory.
            with closing(sqlite3.connect(database)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise UpdateError("Application database backup is not valid")
    else:
        target.mkdir(parents=True)


def preserve_ownership(source: Path, target: Path) -> None:
    """A root-run Docker updater must preserve the unprivileged server's UID."""
    if os.name != "nt" and os.geteuid() == 0:
        for original in (source, *source.rglob("*")):
            metadata = original.lstat()
            copied = target / original.relative_to(source)
            os.chown(copied, metadata.st_uid, metadata.st_gid, follow_symlinks=False)
