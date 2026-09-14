from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QDialog
import pytest

from papagui_client.application.paths import PlatformFamily, SourceMapping
from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.gui.dialogs import OnboardingDialog
from papagui_client.gui.main import ClientMainWindow


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _settings(tmp_path: Path, **changes) -> ClientSettings:
    values = {
        "server_url": "http://127.0.0.1:8765",
        "data_root": tmp_path / "client-data",
        "api_token": "old-token",
        "sync_interval_seconds": 3600,
        "theme": ClientTheme.DARK,
    }
    values.update(changes)
    return ClientSettings(**values)


def test_onboarding_restores_v041_structure_and_builds_client_settings(
    application, tmp_path
):
    source = tmp_path / "mounted-source"
    source.mkdir()
    current = _settings(tmp_path)
    dialog = OnboardingDialog(current, platform=PlatformFamily.LINUX)

    assert dialog.objectName() == "OnboardingDialog"
    assert dialog.isModal()
    assert dialog.minimumWidth() == 600
    assert "Willkommen bei PapaGUI" in dialog.findChild(
        type(dialog.error_label), "PopupTitle"
    ).text()
    assert dialog.cancel_button.text() == "Später"
    assert dialog.browse_button.text() == "Durchsuchen"
    assert dialog.finish_button.text() == "Einrichtung abschließen"

    dialog.server_url_input.setText("https://server.test")
    dialog.api_token_input.setText("new-token")
    dialog.source_id_input.setText("primary")
    dialog.path_input.setText(str(source))
    dialog.accept_settings()

    assert dialog.result() == QDialog.DialogCode.Accepted
    result = dialog.settings()
    assert result.server_url == "https://server.test"
    assert result.api_token == "new-token"
    assert result.data_root == current.data_root
    assert result.sync_interval_seconds == current.sync_interval_seconds
    assert result.theme is ClientTheme.DARK
    assert result.source_mappings == (
        SourceMapping("primary", linux=str(source.resolve())),
    )
    dialog.close()


def test_onboarding_inline_validation_and_error_reset(application, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    dialog = OnboardingDialog(_settings(tmp_path), platform=PlatformFamily.LINUX)

    dialog.path_input.clear()
    dialog.accept_settings()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "wählen" in dialog.error_label.text()

    dialog.path_input.setText(str(tmp_path / "missing"))
    dialog.accept_settings()
    assert "nicht erreichbar" in dialog.error_label.text()

    dialog.path_input.setText(str(source))
    dialog.server_url_input.clear()
    dialog.accept_settings()
    assert "Server-URL" in dialog.error_label.text()

    dialog.server_url_input.setText("https://server.test")
    assert not dialog.error_label.text()
    dialog.source_id_input.clear()
    dialog.accept_settings()
    assert "source_id" in dialog.error_label.text()
    dialog.close()


def test_onboarding_browse_cancel_selection_and_platform_mapping(
    application, tmp_path, monkeypatch
):
    selected = tmp_path / "selected"
    selected.mkdir()
    current = _settings(
        tmp_path,
        source_mappings=(
            SourceMapping(
                "archive",
                windows="Z:/Archive",
                macos="/Volumes/Old",
                linux="/mnt/archive",
            ),
        ),
    )
    dialog = OnboardingDialog(current, platform=PlatformFamily.MACOS)
    assert dialog.source_id_input.text() == "archive"
    assert dialog.path_input.text() == "/Volumes/Old"
    assert dialog.path_input.accessibleName() == "Lokaler macOS-Pfad"

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: "")
    dialog.choose_path()
    assert dialog.path_input.text() == "/Volumes/Old"
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *_args: str(selected)
    )
    dialog.choose_path()
    assert dialog.path_input.text() == str(selected)

    dialog.accept_settings()
    mapping = dialog.settings().source_mappings[0]
    assert mapping.windows == "Z:/Archive"
    assert mapping.macos == str(selected.resolve())
    assert mapping.linux == "/mnt/archive"
    dialog.close()


def test_onboarding_honors_environment_overrides(application, tmp_path):
    dialog = OnboardingDialog(
        _settings(tmp_path),
        sources={
            "server_url": "environment:PAPAGUI_INDEX_SERVER_URL",
            "api_token": "environment:PAPAGUI_API_TOKEN",
            "source_mappings": "environment:PAPAGUI_SOURCE_MAPPINGS",
        },
    )
    assert not dialog.server_url_input.isEnabled()
    assert not dialog.api_token_input.isEnabled()
    assert not dialog.source_id_input.isEnabled()
    assert not dialog.path_input.isEnabled()
    assert not dialog.browse_button.isEnabled()
    dialog.close()


def test_main_uses_dedicated_onboarding_and_updates_theme_without_reloading_catalog(
    application, qtbot, tmp_path, monkeypatch
):
    from tests.client.test_gui_lifecycle_edges import make_container

    source = tmp_path / "source"
    source.mkdir()
    container = make_container(tmp_path)
    container.settings = _settings(tmp_path)
    container.config_sources = {}
    container.onboarding_required = False
    configured = _settings(
        tmp_path,
        server_url="https://configured.test",
        source_mappings=(SourceMapping("primary", linux=str(source)),),
        theme=ClientTheme.LIGHT,
    )
    saved = []

    def save_client_settings(settings):
        saved.append(settings)
        container.settings = settings
        return settings

    container.persist_client_settings = lambda value: value
    container.apply_client_settings = save_client_settings
    window = ClientMainWindow(container, automatic_sync=False)

    class AcceptedOnboarding:
        class DialogCode:
            Accepted = 1

        def __init__(self, *_args, **kwargs):
            self.kwargs = kwargs

        def exec(self):
            return self.DialogCode.Accepted

        def settings(self):
            return configured

    class UnexpectedSettingsDialog:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("Manual settings dialog must not handle onboarding")

    monkeypatch.setattr(
        "papagui_client.gui.main.OnboardingDialog", AcceptedOnboarding
    )
    monkeypatch.setattr(
        "papagui_client.gui.main.ClientSettingsDialog", UnexpectedSettingsDialog
    )
    themes = []
    metadata = []
    monkeypatch.setattr(
        "papagui_client.gui.main.apply_application_theme",
        lambda _application, theme: themes.append(theme),
    )
    window._refresh_search_metadata = lambda: metadata.append(True)

    window.open_settings(onboarding=True)
    qtbot.waitUntil(lambda: not window._settings_saving)

    assert saved == [configured]
    assert themes == [ClientTheme.LIGHT]
    assert metadata == []
    assert "https://configured.test" in window.server_label.text()
    assert "Einrichtung abgeschlossen" in window.status_bar.status_label.text()
    window.close()

