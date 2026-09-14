from __future__ import annotations

from dataclasses import replace
import json
import pytest

from papagui_client.adapters.json_config import JsonClientConfigRepository
from papagui_client.config import ClientSettings
from papagui_client.gui.settings import ClientSettingsDialog
from tools.prepare_client_start import DEFAULT_VARIABLES, prepare, uses_local_server


def environment(tmp_path):
    return {
        "PAPAGUI_CLIENT_DATA_ROOT": str(tmp_path),
        "PAPAGUI_INDEX_SERVER_URL": "http://127.0.0.1:8765",
        "PAPAGUI_API_TOKEN": "synthetic-local-token",
        "PAPAGUI_SOURCE_MAPPINGS": json.dumps({"primary": {"windows": str(tmp_path / "files")}}),
    }


def test_first_start_is_editable_and_restart_retains_saved_nas_connection(tmp_path, qtbot):
    values = environment(tmp_path)
    preview = prepare(values, DEFAULT_VARIABLES)
    assert uses_local_server(preview.settings, values)
    assert not (tmp_path / "client-config.json").exists()
    initial = prepare(values, DEFAULT_VARIABLES, seed=True)
    dialog = ClientSettingsDialog(initial.settings, sources=initial.sources)
    qtbot.addWidget(dialog)
    assert dialog.connection_page.server_url.isEnabled()
    assert dialog.connection_page.api_token.isEnabled()
    assert dialog.mapping_page.table.isEnabled()
    saved = replace(initial.settings, server_url="https://nas.test", api_token="synthetic-nas-token")
    repository = JsonClientConfigRepository(tmp_path / "client-config.json")
    repository.save(saved)
    before = repository.path.read_bytes()

    restarted = prepare(values, DEFAULT_VARIABLES, seed=True)
    assert restarted.settings == saved
    assert not uses_local_server(restarted.settings, values)
    assert repository.path.read_bytes() == before


def test_explicit_environment_remains_an_override_without_persisting_it(tmp_path):
    values = environment(tmp_path)
    values["PAPAGUI_INDEX_SERVER_URL"] = "https://managed.test"
    defaults = ("PAPAGUI_SOURCE_MAPPINGS",)
    resolved = prepare(values, defaults, seed=True)
    assert resolved.settings.server_url == "https://managed.test"
    assert resolved.sources["server_url"] == "environment:PAPAGUI_INDEX_SERVER_URL"
    assert resolved.sources["api_token"] == "environment:PAPAGUI_API_TOKEN"
    stored = JsonClientConfigRepository(tmp_path / "client-config.json").load()
    assert stored.api_token == ""
    assert stored.server_url == "http://127.0.0.1:8765"


@pytest.mark.parametrize("url,port,expected", [
    ("http://localhost:8765/", "8765", True),
    ("http://[::1]:8765", "8765", True),
    ("http://127.0.0.1:9000", "9000", True),
    ("http://127.0.0.1:9000", "8765", False),
    ("https://localhost:8765", "8765", False),
    ("http://localhost:8765/proxy", "8765", False),
    ("http://nas.test:8765", "8765", False),
    ("http://localhost:8765/?server=nas", "8765", False),
])
def test_only_the_managed_local_endpoint_starts_docker(url, port, expected):
    assert uses_local_server(ClientSettings(server_url=url), {"PAPAGUI_API_PORT": port}) is expected


def test_custom_configuration_path_and_explicit_override_on_restart(tmp_path):
    values = environment(tmp_path)
    path = tmp_path / "config/custom.json"
    values["PAPAGUI_CLIENT_CONFIG_PATH"] = str(path)
    prepare(values, DEFAULT_VARIABLES, seed=True)
    assert path.is_file()
    assert not (tmp_path / "client-config.json").exists()
    values["PAPAGUI_INDEX_SERVER_URL"] = "https://override.test"
    resolved = prepare(values, ("PAPAGUI_API_TOKEN", "PAPAGUI_SOURCE_MAPPINGS"))
    assert resolved.settings.server_url == "https://override.test"
    assert resolved.settings.api_token == "synthetic-local-token"


def test_broken_config_is_not_overwritten(tmp_path):
    path = tmp_path / "client-config.json"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Clientkonfiguration"):
        prepare(environment(tmp_path), DEFAULT_VARIABLES, seed=True)
    assert path.read_text() == "broken"
