from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from papagui_client.application.paths import SourceMapping
from papagui_client.config import ClientSettings
from papagui_client.gui import main as main_module
from papagui_client.gui.legacy_models import ApplicationStatistics
from papagui_client.gui.main import ClientMainWindow
from papagui_client.gui.settings import ClientSettingsDialog


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _settings(tmp_path) -> ClientSettings:
    return ClientSettings(
        server_url="https://server.test",
        data_root=tmp_path,
        source_mappings=(SourceMapping("primary", linux="/mnt/source"),),
    )


def test_statistics_page_restores_live_values_and_keeps_config_card(
    application,
    tmp_path,
):
    statistics = ApplicationStatistics(
        customer_count=12,
        contact_count=23,
        project_count=34,
        service_count=5,
        file_count=456,
        total_file_size=2_048,
    )
    dialog = ClientSettingsDialog(_settings(tmp_path), statistics=statistics)

    values = dialog.statistics_page.statistics_widget._value_labels
    assert values["customer_count"].text() == "12"
    assert values["contact_count"].text() == "23"
    assert values["project_count"].text() == "34"
    assert values["file_count"].text() == "456"
    assert values["total_file_size"].text() == "2.0 KB"
    assert "Lokale Clientkonfiguration" in {
        label.text() for label in dialog.statistics_page.findChildren(QLabel)
    }

    dialog.set_statistics(None, "lokale Kopie nicht lesbar")
    assert "lokale Kopie nicht lesbar" in (
        dialog.statistics_page.statistics_widget.status_label.text()
    )
    dialog.close()


def test_index_server_button_emits_navigation_only(application, tmp_path):
    dialog = ClientSettingsDialog(_settings(tmp_path))
    requested = []
    dialog.indexServerRequested.connect(lambda: requested.append(True))

    assert dialog.sync_page.open_index_server_button.text() == "Indexserver öffnen"
    dialog.sync_page.open_index_server_button.click()

    assert requested == [True]
    assert "keinen Indexlauf" in dialog.sync_page.open_index_server_button.toolTip()
    dialog.close()


def test_main_window_passes_statistics_and_connects_independent_tray(monkeypatch):
    statistics = ApplicationStatistics(customer_count=7, file_count=11)
    captured = {}
    opened = Mock()

    class _Signal:
        def connect(self, callback):
            captured["callback"] = callback

    class _RejectedDialog:
        class DialogCode:
            Accepted = 1

        indexServerRequested = _Signal()

        def exec(self):
            return 0

    def build_dialog(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _RejectedDialog()

    monkeypatch.setattr(main_module, "ClientSettingsDialog", build_dialog)
    window = SimpleNamespace(
        _container=SimpleNamespace(
            settings=object(),
            config_sources={"server_url": "file"},
        ),
        _application_statistics=statistics,
        _show_index_tray=opened,
    )

    ClientMainWindow.open_settings(window)

    assert captured["kwargs"]["statistics"] is statistics
    assert captured["kwargs"]["sources"] == {"server_url": "file"}
    captured["callback"]()
    opened.assert_called_once_with()


def test_main_window_retains_statistics_computed_from_local_read_models():
    customer = SimpleNamespace(
        contacts=(object(), object()),
        service_types=("Beratung", "Planung", "Beratung"),
    )
    search = SimpleNamespace(
        project_roots=lambda: (object(), object(), object()),
        folders=lambda: (
            SimpleNamespace(folder=SimpleNamespace(file_count=4, total_size=1_024)),
            SimpleNamespace(folder=SimpleNamespace(file_count=6, total_size=2_048)),
        ),
    )
    page = SimpleNamespace(set_statistics=Mock())
    window = SimpleNamespace(
        _customer_views=(SimpleNamespace(customer=customer),),
        _container=SimpleNamespace(search=search),
        search_page=page,
    )

    ClientMainWindow._refresh_statistics(window)

    statistics = window._application_statistics
    assert statistics.customer_count == 1
    assert statistics.contact_count == 2
    assert statistics.project_count == 3
    assert statistics.service_count == 2
    assert statistics.file_count == 10
    assert statistics.total_file_size == 3_072
    page.set_statistics.assert_called_once_with(statistics)
