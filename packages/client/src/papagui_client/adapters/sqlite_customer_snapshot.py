"""Immutable, read-only customer generation adapter."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Callable

from papagui_contracts import Customer

from .sqlite_customer_mapping import hydrate_customer


class CustomerSnapshotUnavailableError(RuntimeError):
    pass


class SQLiteCustomerSnapshot:
    """Hydrate customer contracts from a generation database opened read-only."""

    def __init__(self, database: Path | Callable[[], Path]):
        self._database = database if callable(database) else lambda: database

    def list_customers(self) -> list[Customer]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM customers ORDER BY display_name COLLATE NOCASE"
            ).fetchall()
            return [hydrate_customer(connection, row) for row in rows]

    def get_customer(self, customer_id: int) -> Customer | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM customers WHERE id=?", (customer_id,)
            ).fetchone()
            return hydrate_customer(connection, row) if row is not None else None

    def _connect(self) -> sqlite3.Connection:
        database = self._database().resolve()
        if not database.is_file():
            raise CustomerSnapshotUnavailableError(
                f"customer snapshot not found: {database}"
            )
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def customer_suggestions_page(self, customer_id: int, status: str = "pending", *,
                                  limit: int = 30, offset: int = 0):
        from .sqlite_suggestion_snapshot import read_suggestion_page

        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid suggestion page")
        with closing(self._connect()) as connection:
            return read_suggestion_page(connection, customer_id, status, limit, offset)
