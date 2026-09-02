"""SQLite transaction boundary composing focused customer repositories."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from papagui_server.adapters.customer_projects import SqliteCustomerProjectRepository
from papagui_server.adapters.customer_repository import SqliteCustomerRepository
from papagui_server.adapters.customer_schema import initialize_customer_schema
from papagui_server.adapters.customer_suggestions import (
    SqliteCustomerSuggestionRepository,
)
from papagui_server.adapters.recognition_repository import SqliteRecognitionRepository


class SqliteCustomerUnitOfWork:
    """One `BEGIN IMMEDIATE` transaction for every write use case."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._connection: sqlite3.Connection | None = None
        self.customers: SqliteCustomerRepository
        self.projects: SqliteCustomerProjectRepository
        self.suggestions: SqliteCustomerSuggestionRepository
        self.recognition: SqliteRecognitionRepository
        self._committed = False

    def __enter__(self) -> "SqliteCustomerUnitOfWork":
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=30, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            initialize_customer_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
        except Exception:
            connection.close()
            raise
        self._connection = connection
        self.projects = SqliteCustomerProjectRepository(connection)
        self.suggestions = SqliteCustomerSuggestionRepository(connection)
        self.recognition = SqliteRecognitionRepository(connection)
        self.customers = SqliteCustomerRepository(
            connection, self.projects, self.suggestions
        )
        return self

    def commit(self) -> None:
        if self._connection is None:
            raise RuntimeError("Die Unit of Work wurde nicht geöffnet.")
        self._connection.commit()
        self._committed = True

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._connection is None:
            return
        if not self._committed or exc_type is not None:
            self._connection.rollback()
        self._connection.close()
        self._connection = None


class SqliteCustomerUnitOfWorkFactory:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def __call__(self) -> SqliteCustomerUnitOfWork:
        return SqliteCustomerUnitOfWork(self.database_path)

    def initialize(self) -> None:
        with self() as work:
            work.commit()
