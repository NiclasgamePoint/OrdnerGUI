from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from papagui_server.adapters.security import (
    AdminSessionManager,
    ClientTokenAuthenticator,
    SecurityConfiguration,
    SecurityConfigurationStore,
    hash_admin_password,
)
from papagui_server.adapters.settings import JsonSettingsRepository
from papagui_server.application.settings import SettingsApplicationService
from papagui_server.composition import RuntimeConfiguration
from papagui_server.domain.errors import AdminAuthenticationError
from papagui_server.domain.models import MutableRunState, ServerSettings


def test_client_tokens_and_security_configuration_value() -> None:
    configuration = SecurityConfiguration("token", "hash", True, 15)
    assert configuration.client_token == "token"
    with pytest.raises(ValueError, match="PAPAGUI_API_TOKEN"):
        ClientTokenAuthenticator("")
    insecure = ClientTokenAuthenticator("", allow_insecure=True)
    assert insecure.accepts(None)
    secured = ClientTokenAuthenticator("secret")
    assert secured.accepts("secret")
    assert not secured.accepts(None)
    assert not secured.accepts("wrong")


def test_argon2_admin_sessions_expire_revoke_and_reset() -> None:
    with pytest.raises(ValueError, match="mindestens 12"):
        hash_admin_password("short")
    password_hash = hash_admin_password("secure-password-123")
    assert password_hash.startswith("$argon2id$")
    sessions = AdminSessionManager(password_hash, ttl=timedelta(minutes=1))
    assert sessions.configured
    with pytest.raises(AdminAuthenticationError):
        sessions.login("wrong-password")
    token, expires_at = sessions.login("secure-password-123")
    assert datetime.fromisoformat(expires_at) > datetime.now(timezone.utc)
    assert sessions.accepts(token)
    assert not sessions.accepts("")
    sessions._sessions[token] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert not sessions.accepts(token)
    token, _ = sessions.login("secure-password-123")
    sessions.revoke(token)
    assert not sessions.accepts(token)
    token, _ = sessions.login("secure-password-123")
    sessions.replace_password_hash(hash_admin_password("replacement-password"))
    assert not sessions.accepts(token)
    with pytest.raises(AdminAuthenticationError):
        sessions.login("secure-password-123")
    unconfigured = AdminSessionManager("")
    assert not unconfigured.configured
    with pytest.raises(AdminAuthenticationError, match="kein Adminpasswort"):
        unconfigured.login("anything")
    malformed = AdminSessionManager("not-an-argon-hash")
    with pytest.raises(AdminAuthenticationError, match="ungültig"):
        malformed.login("anything")


def test_security_store_all_initialization_paths(tmp_path: Path) -> None:
    path = tmp_path / "config" / "security.json"
    store = SecurityConfigurationStore(path)
    assert store.admin_password_hash() == ""
    path.parent.mkdir()
    path.write_text("not json", encoding="utf-8")
    assert store.admin_password_hash() == ""
    with pytest.raises(ValueError, match="Argon2id"):
        store.save_admin_password_hash("bad")
    with pytest.raises(ValueError, match="PAPAGUI_ADMIN_PASSWORD_HASH"):
        store.initialize(configured_hash="bad")
    generated = store.initialize(bootstrap_password="bootstrap-password")
    assert generated.startswith("$argon2id$")
    assert store.admin_password_hash() == generated
    assert store.initialize() == generated
    configured = hash_admin_password("configured-password")
    assert store.initialize(configured_hash=configured) == configured
    assert json.loads(path.read_text(encoding="utf-8"))["admin_password_hash"] == configured
    empty_store = SecurityConfigurationStore(tmp_path / "empty" / "security.json")
    assert empty_store.initialize() == ""


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
    hash_file = tmp_path / "admin-password-hash"
    password_hash = hash_admin_password("file-admin-password")
    hash_file.write_text(password_hash + "\n", encoding="utf-8")
    monkeypatch.setenv("PAPAGUI_API_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PAPAGUI_ADMIN_PASSWORD_HASH_FILE", str(hash_file))
    monkeypatch.delenv("PAPAGUI_API_TOKEN", raising=False)
    monkeypatch.delenv("PAPAGUI_ADMIN_PASSWORD_HASH", raising=False)
    values = RuntimeConfiguration.from_environment()
    assert values.client_token == "file-token-123"
    assert values.admin_password_hash == password_hash

    monkeypatch.setenv("PAPAGUI_API_TOKEN", "direct-token")
    monkeypatch.setenv("PAPAGUI_ADMIN_PASSWORD_HASH", "direct-hash")
    monkeypatch.setenv("PAPAGUI_API_TOKEN_FILE", str(tmp_path / "missing-token"))
    monkeypatch.setenv("PAPAGUI_ADMIN_PASSWORD_HASH_FILE", str(tmp_path / "missing-hash"))
    values = RuntimeConfiguration.from_environment()
    assert values.client_token == "direct-token"
    assert values.admin_password_hash == "direct-hash"


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
