"""Avoid repeated whole-database migration work in short evidence transactions."""
import sqlite3

from papagui_server.adapters.customer_schema import initialize_customer_schema
from papagui_server.adapters.settings import JsonSettingsRepository
from papagui_server.domain.models import ServerSettings


def test_current_schema_initialization_only_checks_its_version(tmp_path):
    connection = sqlite3.connect(tmp_path / "synthetic.db")
    connection.row_factory = sqlite3.Row
    initialize_customer_schema(connection)
    statements = []
    connection.set_trace_callback(statements.append)
    initialize_customer_schema(connection)
    assert len(statements) == 2
    assert all(statement.startswith("SELECT") for statement in statements)
    connection.close()


def test_image_defaults_upgrade_preserves_custom_file_selection(tmp_path):
    import json
    config = tmp_path / "settings.json"
    old = "pdf,doc,docx,xls,xlsx,txt,csv,md,log,json,xml,yaml,yml,ini"
    config.write_text(json.dumps({"content_extensions": old}))
    assert JsonSettingsRepository(config).load().content_extensions == ServerSettings().content_extensions
    for custom in ("pdf,docx", "csv"):
        config.write_text(json.dumps({"content_extensions": custom}))
        assert JsonSettingsRepository(config).load().content_extensions == custom
    # A deliberate selection in the new settings schema is no longer a legacy default.
    config.write_text(json.dumps({"content_extensions": old, "recognition_pipeline_enabled": False}))
    assert JsonSettingsRepository(config).load().content_extensions == old
