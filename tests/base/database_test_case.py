"""Database fixture base class."""

from __future__ import annotations

import sqlite3

from tests.base.test_case import PapaGuiTestCase


class DatabaseTestCase(PapaGuiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database_path = self.temp_path / "test.db"

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        self.addCleanup(connection.close)
        return connection
