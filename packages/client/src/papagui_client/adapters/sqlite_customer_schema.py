"""SQLite connection and schema migrations for client-owned offline state."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3


def connect_outbox(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def initialize_offline_schema(database: Path) -> None:
    """Create/migrate the client database without discarding queued intent."""
    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_outbox(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS customer_outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT UNIQUE NOT NULL,
                aggregate_key TEXT NOT NULL,
                operation TEXT NOT NULL,
                expected_revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                conflict_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_overlay (
                aggregate_key TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                payload_json TEXT,
                confirmed INTEGER NOT NULL DEFAULT 0,
                conflict_json TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS journal_outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT UNIQUE NOT NULL,
                aggregate_key TEXT NOT NULL,
                customer_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                target_id INTEGER,
                expected_revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                conflict_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS journal_overlay (
                aggregate_key TEXT PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                payload_json TEXT,
                confirmed INTEGER NOT NULL DEFAULT 0,
                confirmed_revision INTEGER,
                conflict_json TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS client_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            DROP INDEX IF EXISTS idx_customer_outbox_pending_aggregate;
            CREATE INDEX IF NOT EXISTS idx_customer_outbox_aggregate_sequence
                ON customer_outbox(aggregate_key, sequence);
            CREATE INDEX IF NOT EXISTS idx_journal_outbox_aggregate_sequence
                ON journal_outbox(aggregate_key, sequence);
            """
        )
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(journal_overlay)").fetchall()
        }
        if "confirmed_revision" not in columns:
            connection.execute(
                "ALTER TABLE journal_overlay ADD COLUMN confirmed_revision INTEGER"
            )
        connection.commit()
