from __future__ import annotations

import json
from pathlib import Path

import pytest

from papagui_server.adapters.security import ClientTokenAuthenticator
from papagui_server.adapters.settings import JsonSettingsRepository
from papagui_server.application.settings import SettingsApplicationService
from papagui_server.composition import RuntimeConfiguration
from papagui_server.domain.models import MutableRunState, ServerSettings


def test_client_token_authentication() -> None:
    with pytest.raises(ValueError, match="PAPAGUI_API_TOKEN"):
        ClientTokenAuthenticator("")
    insecure = ClientTokenAuthenticator("", allow_insecure=True)
    assert insecure.accepts(None)
    secured = ClientTokenAuthenticator("secret")
    assert secured.accepts("secret")
    assert not secured.accepts(None)
    assert not secured.accepts("wrong")


@pytest.mark.parametrize(
    "values",
    [
        {"interval_seconds": 899},
        {"interval_seconds": 172_801},
        {"minimum_customer_year": 1899},
        {"max_file_size_mb": 0},
        {"max_extracted_characters": 0},
        {"ocr_max_pages": 0},
        {"ocr_timeout_seconds": 0},
        {"ocr_max_pages": 6, "ocr_extended_max_pages": 5},
        {"resource_profile": "turbo"},
    ],
)
def test_server_settings_boundaries(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ServerSettings.from_mapping(values)


def test_settings_repository_service_and_derived_values(tmp_path: Path) -> None:
    path = tmp_path / "config" / "settings.json"
    defaults = ServerSettings(interval_seconds=900)
    repository = JsonSettingsRepository(path, defaults)
    assert repository.load() == defaults
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")
    assert repository.load() == defaults
    path.unlink()
    path.mkdir()
    assert repository.load() == defaults
    path.rmdir()

    observed: list[ServerSettings] = []
    service = SettingsApplicationService(repository, observed.append)
    updated = service.update(
        {
            "interval_seconds": 172_800,
            "excluded_folders": " .GIT, tmp ,,",
            "content_extensions": ".PDF, txt,",
            "automatic_runs_enabled": False,
        }
    )
    assert service.get() == updated
    assert updated.excluded_folder_names == frozenset({".git", "tmp"})
    assert updated.indexed_content_types == frozenset({"pdf", "txt"})
    assert not updated.automatic_run_enabled
    assert observed == [updated]
    assert json.loads(path.read_text(encoding="utf-8"))["interval_seconds"] == 172_800
    with pytest.raises(ValueError, match="Unbekannte"):
        service.update({"does_not_exist": True})

    no_callback = SettingsApplicationService(repository)
    assert no_callback.update({"interval_seconds": 900}).interval_seconds == 900


def test_mutable_run_state_snapshot_is_immutable_copy() -> None:
    state = MutableRunState(run_id="run", extra={"value": 1})
    snapshot = state.snapshot()
    state.run_id = "changed"
    assert snapshot.run_id == "run"
    assert snapshot.to_dict()["phase"] == "idle"


def test_runtime_secrets_support_files_and_direct_values_take_priority(
    tmp_path: Path, monkeypatch
) -> None:
    token_file = tmp_path / "api-token"
    token_file.write_text("  file-token-123  \n", encoding="utf-8")
    monkeypatch.setenv("PAPAGUI_API_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("PAPAGUI_API_TOKEN", raising=False)
    values = RuntimeConfiguration.from_environment()
    assert values.client_token == "file-token-123"

    monkeypatch.setenv("PAPAGUI_API_TOKEN", "direct-token")
    monkeypatch.setenv("PAPAGUI_API_TOKEN_FILE", str(tmp_path / "missing-token"))
    values = RuntimeConfiguration.from_environment()
    assert values.client_token == "direct-token"


@pytest.mark.parametrize("content", [None, "  \n"])
def test_explicit_secret_file_fails_closed(
    tmp_path: Path, monkeypatch, content: str | None
) -> None:
    path = tmp_path / "secret"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.delenv("PAPAGUI_API_TOKEN", raising=False)
    monkeypatch.setenv("PAPAGUI_API_TOKEN_FILE", str(path))
    with pytest.raises(ValueError, match="PAPAGUI_API_TOKEN_FILE"):
        RuntimeConfiguration.from_environment()
