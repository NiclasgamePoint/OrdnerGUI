from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from papagui_server.adapters.migration import (
    migrate_customer_database,
    restore_customer_database,
)
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.domain.source_paths import coerce_source_path, source_path_from_uri


def test_existing_customer_database_is_backed_up_before_v2_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "customers.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY, folder_path TEXT UNIQUE NOT NULL, "
        "display_name TEXT NOT NULL, entity_type TEXT NOT NULL DEFAULT 'Unternehmen', "
        "company TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', "
        "phone TEXT NOT NULL DEFAULT '', street TEXT NOT NULL DEFAULT '', "
        "postal_code TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '')"
    )
    connection.execute(
        "INSERT INTO customers(folder_path, display_name) VALUES ('customer://old', 'Alt GmbH')"
    )
    connection.commit()
    connection.close()

    backup = migrate_customer_database(database, tmp_path / "config" / "migration.json")
    assert backup is not None and backup.is_file()
    migrated = sqlite3.connect(database)
    try:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(customers)")}
        assert "revision" in columns
        assert migrated.execute("SELECT display_name FROM customers").fetchone()[0] == "Alt GmbH"
    finally:
        migrated.close()
    assert migrate_customer_database(
        database, tmp_path / "config" / "migration.json"
    ) is None


def test_new_database_migration_and_restore_are_recoverable(tmp_path: Path) -> None:
    database = tmp_path / "data" / "customers.db"
    state = tmp_path / "config" / "migration.json"
    assert migrate_customer_database(database, state) is None
    assert database.is_file()
    assert state.is_file()
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO customers(folder_path, display_name) VALUES ('customer://one', 'One')"
    )
    connection.commit()
    connection.close()
    backup = tmp_path / "backup.db"
    backup.write_bytes(database.read_bytes())
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM customers")
    connection.commit()
    connection.close()
    restore_customer_database(database, backup)
    restored = sqlite3.connect(database)
    try:
        assert restored.execute("SELECT display_name FROM customers").fetchone()[0] == "One"
    finally:
        restored.close()


def test_restore_rejects_missing_and_corrupt_backups(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        restore_customer_database(tmp_path / "customers.db", tmp_path / "missing.db")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(sqlite3.DatabaseError):
        restore_customer_database(tmp_path / "customers.db", corrupt)


def test_failed_migration_does_not_write_success_state(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "customers.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE legacy(value TEXT)")
    connection.execute("INSERT INTO legacy VALUES ('preserved')")
    connection.commit()
    connection.close()
    state = tmp_path / "migration.json"

    def fail(_self) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(
        "papagui_server.adapters.migration.SqliteCustomerUnitOfWorkFactory.initialize",
        fail,
    )
    with pytest.raises(RuntimeError, match="migration failed"):
        migrate_customer_database(database, state)
    assert not state.exists()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT value FROM legacy").fetchone()[0] == "preserved"
    finally:
        connection.close()
    assert len(list((tmp_path / "customer-backups").glob("*.db"))) == 1


def test_absolute_windows_unc_and_posix_paths_are_migrated_portably(
    tmp_path: Path,
) -> None:
    database = tmp_path / "customers.db"
    factory = SqliteCustomerUnitOfWorkFactory(database)
    factory.initialize()
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO customers(folder_path, display_name) VALUES (?, ?)",
        (r"C:\Kunden\Alpha", "Alpha"),
    )
    customer_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        "INSERT INTO customer_folders(customer_id, folder_path) VALUES (?, ?)",
        (customer_id, r"\\NAS01\Papa\Kunden\Alpha"),
    )
    connection.execute(
        """
        INSERT INTO customer_projects(
            customer_id, source_id, relative_path, folder_path, folder_key,
            project_label
        ) VALUES (?, 'primary', '', ?, 'legacy-key', 'Alpha')
        """,
        (customer_id, r"D:\Projekte\2026\Alpha"),
    )
    connection.execute(
        """
        INSERT INTO customer_document_suggestions(
            customer_id, kind, value, source_path, fingerprint, confidence
        ) VALUES (?, 'email', 'a@example.org', ?, 'absolute-suggestion', .9)
        """,
        (customer_id, "/source/Planung/2026/Alpha/Kontakt.txt"),
    )
    connection.commit()
    connection.close()

    factory.initialize()
    connection = sqlite3.connect(database)
    try:
        customer_path = connection.execute(
            "SELECT folder_path FROM customers WHERE id=?", (customer_id,)
        ).fetchone()[0]
        folder_path = connection.execute(
            "SELECT folder_path FROM customer_folders WHERE customer_id=?",
            (customer_id,),
        ).fetchone()[0]
        project = connection.execute(
            "SELECT source_id, relative_path, folder_path FROM customer_projects"
        ).fetchone()
        suggestion = connection.execute(
            "SELECT source_path FROM customer_document_suggestions"
        ).fetchone()[0]
    finally:
        connection.close()
    assert source_path_from_uri(customer_path).source_id == "legacy-c"
    assert source_path_from_uri(folder_path).source_id == "unc-NAS01-Papa"
    assert project[:2] == ("legacy-d", "Projekte/2026/Alpha")
    assert source_path_from_uri(project[2]).relative_path == "Projekte/2026/Alpha"
    assert source_path_from_uri(suggestion).source_id == "primary"
    assert source_path_from_uri(suggestion).relative_path == "Planung/2026/Alpha/Kontakt.txt"


@pytest.mark.parametrize(
    ("value", "source_id", "relative_path"),
    [
        (r"C:\Data\Kunde", "legacy-c", "Data/Kunde"),
        (r"\\server\share\Kunde", "unc-server-share", "Kunde"),
        ("/var/data/Kunde", "legacy-posix", "var/data/Kunde"),
        ("/source/Planung/Kunde", "primary", "Planung/Kunde"),
        ("", "primary", "legacy/unknown"),
    ],
)
def test_source_path_coercion_covers_legacy_platforms(
    value: str, source_id: str, relative_path: str
) -> None:
    source = coerce_source_path(value)
    assert (source.source_id, source.relative_path) == (source_id, relative_path)


def test_source_uri_parser_rejects_non_source_url() -> None:
    with pytest.raises(ValueError):
        source_path_from_uri("https://example.org/file")
