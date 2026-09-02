"""One-time, recoverable migration helpers for existing v0.4 customer data."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from papagui_server.adapters.generations import atomic_json, snapshot_sqlite
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory


def migrate_customer_database(
    database_path: Path, migration_state_path: Path
) -> Path | None:
    """Back up the legacy database once, then run schema changes transactionally."""
    try:
        current = json.loads(migration_state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if current.get("customer_schema") == 2:
        SqliteCustomerUnitOfWorkFactory(database_path).initialize()
        return None

    backup: Path | None = None
    if database_path.is_file() and database_path.stat().st_size:
        backup_root = database_path.parent / "customer-backups"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_root / f"customers-pre-v2-{timestamp}.db"
        snapshot_sqlite(database_path, backup)
    SqliteCustomerUnitOfWorkFactory(database_path).initialize()
    atomic_json(
        migration_state_path,
        {
            "customer_schema": 2,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
            "backup": str(backup) if backup is not None else None,
        },
    )
    return backup


def restore_customer_database(database_path: Path, backup_path: Path) -> None:
    """Atomically restore a verified SQLite migration backup."""
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    connection = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Das Kundenbackup ist beschädigt.")
    finally:
        connection.close()
    temporary = database_path.with_name(f".{database_path.name}.restore.tmp")
    try:
        snapshot_sqlite(backup_path, temporary)
        os.replace(temporary, database_path)
    finally:
        temporary.unlink(missing_ok=True)
