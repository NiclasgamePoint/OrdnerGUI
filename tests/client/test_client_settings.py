from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from papagui_client.adapters.json_config import (
    ClientConfigError,
    ClientConfigSecurityWarning,
    JsonClientConfigRepository,
)
from papagui_client.application.paths import SourceMapping
from papagui_client.composition import ClientContainer, build_client
from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.gui.main import ClientMainWindow, forced_fullscreen, show_main_window
from papagui_client.gui.settings import ClientSettingsDialog, theme_stylesheet
from papagui_client.gui.tray import ServerTrayWindow
from papagui_client.presentation.server_settings import ServerSettingsPresenter
from papagui_client.presentation.settings import (
    ClientSettingsPresenter,
    SourceMappingViewModel,
)
from papagui_client.presentation.tray import TrayPresenter


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def configured(tmp_path, **changes):
    values = {
        "server_url": "https://server.test",
        "data_root": tmp_path,
        "api_token": "secret",
        "source_mappings": (
            SourceMapping("primary", "Z:/Data", "/Volumes/Data", "/mnt/data"),
        ),
        "sync_interval_seconds": 3600,
        "theme": ClientTheme.DARK,
    }
    values.update(changes)
    return ClientSettings(**values)


def test_client_settings_dict_roundtrip_and_all_environment_overrides(tmp_path):
    settings = configured(tmp_path)
    restored = ClientSettings.from_dict(settings.to_dict(), data_root=tmp_path)
    assert restored == settings
    resolved = settings.with_environment(
        {
            "PAPAGUI_INDEX_SERVER_URL": "http://override:8765",
            "PAPAGUI_API_TOKEN": "env-token",
            "PAPAGUI_API_TIMEOUT_SECONDS": "3",
            "PAPAGUI_SYNC_INTERVAL_SECONDS": "900",
            "PAPAGUI_CLIENT_THEME": "light",
            "PAPAGUI_CLIENT_DATA_ROOT": str(tmp_path / "override"),
            "PAPAGUI_SOURCE_MAPPINGS": json.dumps(
                {"env": {"linux": "/srv/env"}}
            ),
        }
    )
    assert resolved.settings.server_url == "http://override:8765"
    assert resolved.settings.api_token == "env-token"
    assert resolved.settings.timeout_seconds == 3
    assert resolved.settings.sync_interval_seconds == 900
    assert resolved.settings.theme is ClientTheme.LIGHT
    assert resolved.settings.data_root == tmp_path / "override"
    assert resolved.settings.source_mappings[0].source_id == "env"
    assert all(value.startswith("environment:") for value in resolved.sources.values())

    for raw in ("broken", "[]"):
        with pytest.raises(ValueError):
            settings.with_environment({"PAPAGUI_SOURCE_MAPPINGS": raw})
    with pytest.raises(ValueError):
        ClientSettings.from_dict({"source_mappings": []}, data_root=tmp_path)
    with pytest.raises(ValueError, match="theme"):
        ClientSettings(data_root=tmp_path, theme="neon")


def test_json_config_repository_save_load_permissions_and_defaults(tmp_path):
    path = tmp_path / "config" / "client-config.json"
    repository = JsonClientConfigRepository(path, data_root=tmp_path / "data")
    assert repository.load().data_root == (tmp_path / "data").resolve()
    value = configured(tmp_path / "data")
    repository.save(value)
    assert repository.load() == configured((tmp_path / "data").resolve())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["source_mappings"]["primary"]["windows"] == "Z:/Data"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert not tuple(path.parent.glob(".*.tmp"))


def test_json_config_repository_migrates_v1_aliases_atomically(tmp_path):
    path = tmp_path / "client-config.json"
    path.write_text(
        json.dumps(
            {
                "index_server_url": "https://legacy.test",
                "token": "old-token",
                "source_paths": {
                    "legacy": "/mnt/legacy",
                    "mapped": {"windows": "Y:/", "linux": "/mnt/mapped"},
                },
                "sync_interval_minutes": 30,
                "theme": "light",
            }
        ),
        encoding="utf-8",
    )
    repository = JsonClientConfigRepository(path)
    settings = repository.load()
    assert settings.server_url == "https://legacy.test"
    assert settings.api_token == "old-token"
    assert settings.sync_interval_seconds == 1800
    assert settings.source_mappings[0].linux == "/mnt/legacy"
    assert json.loads(path.read_text())["schema_version"] == 2

    # Explicit v1 seconds and malformed source-path collections take the safe defaults.
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "server_url": "http://localhost",
                "source_paths": [],
                "sync_interval_seconds": 900,
            }
        ),
        encoding="utf-8",
    )
    assert repository.load().source_mappings == ()


@pytest.mark.parametrize("payload", ("broken", "[]", '{"schema_version":99}'))
def test_json_config_repository_reports_invalid_documents(tmp_path, payload):
    path = tmp_path / "client-config.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ClientConfigError):
        JsonClientConfigRepository(path).load()


def test_json_config_repository_wraps_atomic_write_errors(tmp_path, monkeypatch):
    repository = JsonClientConfigRepository(tmp_path / "client-config.json")
    monkeypatch.setattr(
        "papagui_client.adapters.json_config.os.replace",
        Mock(side_effect=OSError("read only")),
    )
    with pytest.raises(ClientConfigError, match="read only"):
        repository.save(configured(tmp_path))
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_json_config_repository_hardens_windows_acl_or_reports_failure(
    tmp_path, monkeypatch
):
    path = tmp_path / "client-config.json"
    repository = JsonClientConfigRepository(path)
    acl = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("papagui_client.adapters.json_config.subprocess.run", acl)
    monkeypatch.setenv("USERNAME", "PapaGUI User")
    monkeypatch.setattr("papagui_client.adapters.json_config.os.name", "nt")

    repository.save(configured(tmp_path))
    arguments = acl.call_args.args[0]
    assert arguments[0] == "icacls"
    assert arguments[-1] == "PapaGUI User:(F)"
    assert path.is_file()

    acl.return_value = SimpleNamespace(returncode=5, stdout="", stderr="access denied")
    with pytest.warns(ClientConfigSecurityWarning, match="access denied"):
        repository.save(configured(tmp_path))

    strict_path = tmp_path / "strict.json"
    strict = JsonClientConfigRepository(strict_path, strict_permissions=True)
    with pytest.raises(ClientConfigError, match="Windows-ACL"):
        strict.save(configured(tmp_path))
    assert not strict_path.exists()


def test_client_settings_presenter_view_model_build_and_validation(tmp_path):
    presenter = ClientSettingsPresenter()
    settings = configured(tmp_path)
    model = presenter.present(
        settings,
        {
            "api_token": "environment:PAPAGUI_API_TOKEN",
            "server_url": "persisted",
        },
    )
    assert model.interval_value == 1 and model.interval_unit == "Stunden"
    assert model.theme == "dark"
    assert model.environment_overrides == {"api_token"}
    assert not model.onboarding_required
    assert presenter.present(ClientSettings(data_root=tmp_path)).onboarding_required
    assert presenter.present(
        ClientSettings(server_url="", data_root=tmp_path, source_mappings=settings.source_mappings)
    ).onboarding_required
    assert presenter.interval_fields(899) == (15, "Minuten")
    assert presenter.interval_fields(172_801) == (48, "Stunden")

    rebuilt = presenter.build(
        current=settings,
        server_url=" https://new.test ",
        api_token="new",
        mappings=(SourceMappingViewModel(" archive ", " X:/ ", "", " /mnt/x "),),
        interval_value=45,
        interval_unit="Minuten",
        theme="light",
    )
    assert rebuilt.server_url == "https://new.test"
    assert rebuilt.source_mappings[0].source_id == "archive"
    assert rebuilt.sync_interval_seconds == 2700

    bad_rows = (
        (SourceMappingViewModel("", "X:/"),),
        (SourceMappingViewModel("a"),),
        (SourceMappingViewModel("a", linux="/a"), SourceMappingViewModel("a", linux="/b")),
        (),
    )
    for rows in bad_rows:
        with pytest.raises(ValueError):
            presenter.build(
                current=settings,
                server_url="http://server",
                api_token="",
                mappings=rows,
                interval_value=15,
                interval_unit="Minuten",
                theme="system",
            )
    for value, unit in ((14, "Minuten"), (49, "Stunden"), (15, "Tage")):
        with pytest.raises(ValueError):
            presenter.interval_seconds(value, unit)
    with pytest.raises(ValueError, match="Server-URL"):
        presenter.build(
            current=settings,
            server_url=" ",
            api_token="",
            mappings=(SourceMappingViewModel("a", linux="/a"),),
            interval_value=15,
            interval_unit="Minuten",
            theme="system",
        )


def test_server_settings_presenter_normalizes_every_field_and_rejects_bad_values():
    presenter = ServerSettingsPresenter()
    model = presenter.present({"settings": {"interval_seconds": 7200, "minimum_customer_year": 2020}})
    assert set(model.values) == set(presenter.FIELDS)
    assert (model.interval_value, model.interval_unit) == (2, "Stunden")
    assert model.values["resource_profile"] == "balanced"
    assert presenter.interval_fields(901) == (15, "Minuten")
    assert presenter.interval_fields(200_000) == (48, "Stunden")
    assert presenter.interval_seconds(15, "Minuten") == 900
    assert presenter.interval_seconds(48, "Stunden") == 172_800
    for response in ({"settings": []}, {"minimum_customer_year": 1899}, {"minimum_customer_year": True}):
        with pytest.raises(ValueError):
            presenter.present(response)
    for value, unit in ((14, "Minuten"), (49, "Stunden"), (1, "days")):
        with pytest.raises(ValueError):
            presenter.interval_seconds(value, unit)


def test_client_container_persists_rewires_and_reschedules_active_sync(tmp_path, monkeypatch):
    repository = JsonClientConfigRepository(tmp_path / "client-config.json", data_root=tmp_path)
    initial = configured(tmp_path, theme=ClientTheme.SYSTEM)
    container = ClientContainer(initial, config_repository=repository)

    class Timer:
        def __init__(self):
            self.starts = []
            self.stops = 0

        def start(self, seconds, callback):
            self.starts.append((seconds, callback))

        def stop(self):
            self.stops += 1

    timer = Timer()
    def callback():
        return None

    container.sync.start(timer, callback, immediate=False)
    updated = configured(
        tmp_path,
        server_url="https://other.test",
        sync_interval_seconds=7200,
        theme=ClientTheme.LIGHT,
    )
    effective = container.save_client_settings(updated)
    assert effective == updated
    assert container.settings.server_url == "https://other.test"
    assert container.generation_gateway._transport.base_url == "https://other.test"
    assert container.customer_gateway._transport.token == "secret"
    assert timer.stops == 1
    assert timer.starts[-1] == (7200, callback)
    assert repository.load() == updated

    with pytest.raises(ValueError, match="Neustart"):
        container.reconfigure(configured(tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="Neustart"):
        container.save_client_settings(configured(tmp_path / "elsewhere"))


def test_build_client_resolves_persisted_values_then_environment(tmp_path, monkeypatch):
    repository = JsonClientConfigRepository(tmp_path / "client-config.json", data_root=tmp_path)
    repository.save(configured(tmp_path, server_url="http://persisted"))
    with patch.dict(
        os.environ,
        {
            "PAPAGUI_CLIENT_DATA_ROOT": str(tmp_path),
            "PAPAGUI_INDEX_SERVER_URL": "http://environment",
        },
        clear=True,
    ):
        container = build_client()
    assert container.settings.server_url == "http://environment"
    assert container.config_sources["server_url"].startswith("environment:")
    assert isinstance(build_client(configured(tmp_path / "explicit")), ClientContainer)


def test_settings_dialog_pages_roundtrip_overrides_and_validation(application, tmp_path, monkeypatch):
    settings = configured(tmp_path)
    dialog = ClientSettingsDialog(settings)
    assert dialog.mapping_page.table.rowCount() == 1
    dialog.mapping_page.add_mapping(SourceMappingViewModel("second", linux="/mnt/second"))
    dialog.connection_page.server_url.setText("https://changed.test")
    dialog.sync_page.interval_unit.setCurrentText("Minuten")
    dialog.sync_page.interval_value.setValue(30)
    dialog.sync_page.theme.setCurrentIndex(dialog.sync_page.theme.findData("light"))
    changed = dialog.settings()
    assert changed.server_url == "https://changed.test"
    assert len(changed.source_mappings) == 2
    assert changed.sync_interval_seconds == 1800
    assert changed.theme is ClientTheme.LIGHT

    dialog.mapping_page.table.selectRow(1)
    dialog.mapping_page.remove_selected()
    assert dialog.mapping_page.table.rowCount() == 1
    dialog._validate_and_accept()
    assert dialog.result() == dialog.DialogCode.Accepted
    dialog.close()

    overridden = ClientSettingsDialog(
        settings,
        sources={
            "server_url": "environment:PAPAGUI_INDEX_SERVER_URL",
            "api_token": "environment:PAPAGUI_API_TOKEN",
            "source_mappings": "environment:PAPAGUI_SOURCE_MAPPINGS",
            "sync_interval_seconds": "environment:PAPAGUI_SYNC_INTERVAL_SECONDS",
            "theme": "environment:PAPAGUI_CLIENT_THEME",
        },
        onboarding=True,
    )
    assert not overridden.connection_page.server_url.isEnabled()
    assert not overridden.mapping_page.table.isEnabled()
    assert not overridden.sync_page.interval_value.isEnabled()
    assert overridden.findChild(type(overridden.connection_page.server_url), "missing") is None
    overridden.close()

    invalid = ClientSettingsDialog(settings)
    invalid.mapping_page.table.setRowCount(0)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args))
    invalid._validate_and_accept()
    assert warnings and invalid.result() != invalid.DialogCode.Accepted
    invalid.close()


def test_theme_and_package_native_fullscreen_helpers(monkeypatch):
    assert theme_stylesheet("light", "base") == "base"
    assert "#17242d" in theme_stylesheet("dark", "base")
    with patch.dict(os.environ, {"PAPAGUI_FORCE_FULLSCREEN": "yes"}, clear=True):
        assert forced_fullscreen()
    with patch.dict(os.environ, {}, clear=True):
        assert not forced_fullscreen()

    geometry = SimpleNamespace(topLeft=lambda: (0, 0), size=lambda: (1920, 1080))
    screen = SimpleNamespace(availableGeometry=lambda: geometry)
    window = Mock()
    window.screen.return_value = None
    application = SimpleNamespace(primaryScreen=lambda: screen)
    monkeypatch.setattr("papagui_client.gui.main.forced_fullscreen", lambda: True)
    show_main_window(application, window)
    window.setGeometry.assert_called_once_with(geometry)
    window.showFullScreen.assert_called_once()
    window.reset_mock()
    window.screen.return_value = None
    application = SimpleNamespace(primaryScreen=lambda: None)
    show_main_window(application, window)
    window.setGeometry.assert_not_called()
    window.showFullScreen.assert_called_once()
    monkeypatch.setattr("papagui_client.gui.main.forced_fullscreen", lambda: False)
    window.reset_mock()
    show_main_window(application, window)
    window.show.assert_called_once()
    window.showFullScreen.assert_not_called()


def test_main_window_settings_save_cancel_failure_and_onboarding(application, tmp_path, monkeypatch):
    from tests.client.test_gui_lifecycle_edges import make_container

    container = make_container(tmp_path)
    container.settings = configured(tmp_path)
    container.config_sources = {}
    container.onboarding_required = False
    saved = []
    def save_client_settings(value):
        saved.append(value)
        container.settings = value
        return value

    container.save_client_settings = save_client_settings
    window = ClientMainWindow(container, automatic_sync=False)

    class Dialog:
        class DialogCode:
            Accepted = 1

        result = 0

        def __init__(self, *_args, **kwargs):
            self.kwargs = kwargs

        def exec(self):
            return self.result

        def settings(self):
            return configured(tmp_path, theme=ClientTheme.DARK)

    monkeypatch.setattr("papagui_client.gui.main.ClientSettingsDialog", Dialog)
    window.open_settings()
    assert not saved
    Dialog.result = Dialog.DialogCode.Accepted
    window.open_settings(onboarding=True)
    assert saved and "gespeichert" in window.status.text()
    assert "https://server.test" in window.server_label.text()

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args))
    container.save_client_settings = lambda _value: (_ for _ in ()).throw(OSError("full"))
    window.open_settings()
    assert warnings
    window.close()


def _complete_server_settings() -> dict[str, object]:
    return {
        "automatic_runs_enabled": False,
        "interval_seconds": 2700,
        "daily_reconciliation_enabled": False,
        "content_indexing_enabled": True,
        "content_extensions": "pdf,txt",
        "excluded_folders": ".git,tmp",
        "max_file_size_mb": 321,
        "max_extracted_characters": 123_456,
        "ocr_enabled": False,
        "ocr_max_pages": 4,
        "ocr_extended_max_pages": 12,
        "ocr_extension_threshold": 700,
        "ocr_timeout_seconds": 22,
        "pdf_text_timeout_seconds": 55,
        "resource_profile": "fast",
        "preferred_document_patterns": "angebot,vertrag",
        "priority_documents_per_project": 9,
        "newest_years_first": False,
        "minimum_customer_year": 1995,
    }


def test_tray_complete_settings_roundtrip_dirty_guard_busy_and_validation(
    application, tmp_path, monkeypatch
):
    from tests.client.test_gui_lifecycle_edges import make_container

    container = make_container(tmp_path)
    window = ServerTrayWindow(container)
    window._timer.stop()
    window._ensure_admin = lambda: True
    expected = ServerSettingsPresenter().validate(_complete_server_settings())
    window._apply_settings({"settings": _complete_server_settings()})
    assert window._collect_settings() == expected
    assert not window._settings_dirty
    assert not window.settings_save_button.isEnabled()

    window.max_file_size_mb.setValue(322)
    assert window._settings_dirty
    assert "Ungespeicherte" in window.settings_status.text()
    tasks = []
    window._start_task = tasks.append

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    window.load_settings()
    assert not tasks
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    window.load_settings()
    assert len(tasks) == 1 and window._settings_busy
    window.load_settings()  # Busy guard prevents a second request.
    assert len(tasks) == 1
    window._settings_finished()

    # A save carries every field, keeps the UI busy, and clears the dirty bit
    # only after the server's normalized response has been applied.
    window._apply_settings({"settings": _complete_server_settings()})
    window.max_file_size_mb.setValue(322)
    window.save_settings()
    assert len(tasks) == 2 and window._settings_busy
    tasks[-1].run()
    assert container.server_control.saved_settings["max_file_size_mb"] == 322
    assert set(container.server_control.saved_settings) == set(ServerSettingsPresenter.FIELDS)
    assert not window._settings_busy and not window._settings_dirty
    assert window.settings_status.text() == "Gespeichert"

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args))
    window.ocr_max_pages.setValue(20)
    window.ocr_extended_max_pages.setValue(5)
    window.save_settings()
    assert warnings and "Fehler:" in window.settings_status.text()
    assert len(tasks) == 2
    window.close()


def test_tray_activity_uses_v2_progress_deduplicates_and_retains_fifty(
    application, tmp_path
):
    from tests.client.test_gui_lifecycle_edges import make_container

    payload = {
        "observed_at": "2026-09-02T11:00:00+00:00",
        "index": {
            "run_id": "run-1",
            "state": "running",
            "message": "Dateien werden gelesen",
            "updated_at": "2026-09-02T10:59:59+00:00",
            "progress": {
                "phase": "catalog",
                "processed_items": 12,
                "total_items": 40,
            },
        },
    }
    activity = TrayPresenter.activity(payload)
    assert "catalog" in activity.text
    assert "12 / 40" in activity.text
    assert "Dateien werden gelesen" in activity.text

    window = ServerTrayWindow(make_container(tmp_path))
    window._timer.stop()
    window._record_activity(payload)
    window._record_activity(payload)
    assert window.activity.count() == 1
    for number in range(55):
        window._record_activity(
            {
                "index": {
                    "run_id": f"run-{number + 2}",
                    "state": "completed",
                    "finished_at": f"2026-09-02T12:{number:02d}:00+00:00",
                    "progress": {"phase": "publish", "processed_items": number},
                }
            }
        )
    assert window.activity.count() == 50
    assert "completed" in window.activity.item(0).text()
    assert "publish" in window.activity.item(0).text()
    window.close()
