"""Read-only catalog connection and portable-schema detection."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Callable


class CatalogUnavailableError(RuntimeError):
    pass


DatabasePath = Path | Callable[[], Path]


def provider(database: DatabasePath) -> Callable[[], Path]:
    return database if callable(database) else lambda: database


def read_only(database: Path, label: str = "Catalog") -> sqlite3.Connection:
    database = Path(database).resolve()
    if not database.is_file():
        raise CatalogUnavailableError(f"{label} snapshot not found: {database}")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def columns(connection: sqlite3.Connection, table: str = "files") -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }


def require_portable_files(values: set[str]) -> None:
    missing = {"document_key", "source_id", "relative_path", "filename"} - values
    if missing:
        raise CatalogUnavailableError(
            "Catalog generation is not portable v2; missing columns: "
            + ", ".join(sorted(missing))
        )


def selected_file_columns(values: set[str]) -> str:
    return ", ".join(
        name
        for name in (
            "document_key",
            "source_id",
            "relative_path",
            "filename",
            "file_type",
            "customer_name",
            "project_name",
            "year",
            "modified_date",
            "file_size",
            "domain_folder",
            "time_bucket",
            "relative_dir",
            "folder_id",
            "project_root_id",
        )
        if name in values
    )
