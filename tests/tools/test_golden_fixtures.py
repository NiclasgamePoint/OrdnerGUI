from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from papagui_contracts import GenerationManifest, ServerStatus
from papagui_server.adapters.migration import migrate_customer_database

from tests.tools.golden_fixtures import (
    fixture_path,
    load_json_fixture,
    materialize_sqlite_fixture,
)


def test_versioned_wire_golden_fixtures_parse_with_current_contracts() -> None:
    legacy_status = ServerStatus.from_dict(load_json_fixture("api/v1-server-status.json"))
    legacy_generation = GenerationManifest.from_dict(
        load_json_fixture("generations/v1-combined.json")
    )
    current = GenerationManifest.from_dict(
        load_json_fixture("generations/v2-current.json")
    )

    assert legacy_status.server_version == "0.4.1"
    assert legacy_status.index.progress.legacy_current_path is not None
    assert legacy_generation.legacy_combined is True
    assert legacy_generation.index is not None
    assert legacy_generation.customers is not None
    assert current.legacy_combined is False
    assert current.index is not None
    assert current.customers is not None
    assert current.index.generation != current.customers.generation


def test_v0_4_1_customer_database_fixture_survives_v2_schema_migration(
    tmp_path: Path,
) -> None:
    database = materialize_sqlite_fixture(
        "migration/customers-v0.4.1.sql", tmp_path / "customers.db"
    )
    state = tmp_path / "config" / "migration.json"
    backup = migrate_customer_database(database, state)

    assert backup is not None and backup.is_file()
    assert json.loads(state.read_text(encoding="utf-8"))["customer_schema"] == 2
    with sqlite3.connect(database) as connection:
        customer = connection.execute(
            "SELECT display_name, revision FROM customers WHERE id=1"
        ).fetchone()
        assert customer == ("Beispiel GmbH", 1)
        assert connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_data_suggestions").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM recognition_cases").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_document_suggestions").fetchone()[0] == 2
        assert connection.execute(
            "SELECT folder_path FROM customers WHERE id=1"
        ).fetchone()[0] == "source://primary/Beratung/2024/Beispiel%20GmbH%2C%20Berlin"
        project = connection.execute(
            "SELECT source_id, relative_path, service_type, project_city, provenance "
            "FROM customer_projects WHERE id=1"
        ).fetchone()
        assert project == (
            "primary",
            "Beratung/2024/Beispiel GmbH, Berlin",
            "Beratung",
            "Berlin",
            "folder",
        )
        suggestions = connection.execute(
            "SELECT kind, suggestion_type, source_path, fingerprint, contact_email "
            "FROM customer_document_suggestions ORDER BY id"
        ).fetchall()
        assert suggestions[0] == (
            "email",
            "field",
            "source://primary/Beratung/2024/Beispiel%20GmbH%2C%20Berlin/Angebot.pdf",
            "legacy-suggestion-1",
            "",
        )
        assert suggestions[1] == (
            "contact",
            "contact",
            "source://unc-NAS01-Papa/Beratung/2024/Beispiel%20GmbH%2C%20Berlin/Kontakt.pdf",
            "legacy-contact-2",
            "max@beispiel.invalid",
        )
        recognition = connection.execute(
            "SELECT display_name, project_roots_json, status FROM recognition_cases "
            "WHERE signature='legacy-case-1'"
        ).fetchone()
        assert recognition == (
            "Beispiel GmbH",
            '[{"source_id": "legacy", "relative_path": "cases/legacy-case-1"}]',
            "pending",
        )

    with sqlite3.connect(backup) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(customers)")
        }
        assert "revision" not in columns


def test_fixture_loader_rejects_traversal_and_missing_files() -> None:
    try:
        fixture_path("../../pyproject.toml")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("fixture traversal was accepted")
