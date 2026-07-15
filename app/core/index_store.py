from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3
import uuid


BACKUP_COUNT = 3


def backup_paths(active_path: Path) -> list[Path]:
    return [
        active_path.with_name(f"{active_path.stem}.backup.{index}{active_path.suffix}")
        for index in range(1, BACKUP_COUNT + 1)
    ]


def create_build_path(active_path: Path) -> Path:
    active_path.parent.mkdir(parents=True, exist_ok=True)
    return active_path.with_name(
        f".{active_path.stem}.build-{uuid.uuid4().hex}{active_path.suffix}"
    )


def seed_build_database(active_path: Path, build_path: Path, incremental: bool):
    if build_path.exists():
        build_path.unlink()
    if not incremental or not active_path.exists() or active_path.stat().st_size == 0:
        return

    source = sqlite3.connect(active_path)
    destination = sqlite3.connect(build_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def validate_index(index_path: Path) -> dict[str, str]:
    if not index_path.exists() or index_path.stat().st_size == 0:
        raise ValueError("Der erzeugte Index ist leer")
    connection = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite-Integritätsfehler: {integrity}")
        metadata = dict(connection.execute("SELECT key, value FROM index_metadata"))
        metadata["file_count"] = str(
            connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        )
        metadata["content_count"] = str(
            connection.execute("SELECT COUNT(*) FROM file_content_fts").fetchone()[0]
        )
        return metadata
    finally:
        connection.close()


def activate_index(active_path: Path, build_path: Path):
    """Rotate three backups and atomically move the staged index into place."""
    validate_index(build_path)
    backups = backup_paths(active_path)
    if backups[-1].exists():
        backups[-1].unlink()
    for source, destination in zip(reversed(backups[:-1]), reversed(backups[1:])):
        if source.exists():
            os.replace(source, destination)

    previous_moved = False
    try:
        if active_path.exists():
            os.replace(active_path, backups[0])
            previous_moved = True
        os.replace(build_path, active_path)
    except Exception:
        if previous_moved and backups[0].exists() and not active_path.exists():
            os.replace(backups[0], active_path)
        raise


def available_backups(active_path: Path) -> list[dict[str, str]]:
    entries = []
    for path in backup_paths(active_path):
        if not path.exists():
            continue
        try:
            metadata = validate_index(path)
        except Exception:
            continue
        built_at = metadata.get("built_at", "")
        try:
            display_date = datetime.fromisoformat(built_at).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            display_date = datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%d.%m.%Y %H:%M"
            )
        entries.append({
            "path": str(path),
            "label": f"{display_date} · {metadata.get('file_count', '0')} Dateien",
            "root": metadata.get("index_root", ""),
        })
    return entries


def create_restore_build(active_path: Path, backup_path: Path) -> Path:
    if backup_path not in backup_paths(active_path) or not backup_path.exists():
        raise ValueError("Ungültige Indexsicherung")
    build_path = create_build_path(active_path)
    shutil.copy2(backup_path, build_path)
    validate_index(build_path)
    return build_path
