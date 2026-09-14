from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTabWidget

from papagui_client.application.paths import SourceMapping
from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.gui.settings import ClientSettingsDialog


def test_client_settings_restore_legacy_navigation_without_mixing_server_code(
    qtbot, tmp_path
):
    settings = ClientSettings(
        server_url="https://server.test",
        data_root=tmp_path,
        api_token="secret",
        source_mappings=(SourceMapping("primary", linux="/mnt/data"),),
        sync_interval_seconds=3_600,
        theme=ClientTheme.DARK,
    )
    dialog = ClientSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert [dialog.nav_list.item(row).text() for row in range(dialog.nav_list.count())] == [
        "Allgemein",
        "Indexierung",
        "Suche",
        "Kundenerkennung",
        "Statistik",
        "Aussehen",
    ]
    assert dialog.stack.count() == 6
    assert dialog.findChild(QTabWidget) is None
    assert dialog.connection_page.server_url.text() == "https://server.test"
    assert dialog.mapping_page.table.rowCount() == 1
    assert dialog.sync_page.interval_value.value() == 1
    assert dialog.sync_page.interval_unit.currentText() == "Stunden"
    visible_copy = " ".join(
        label.text() for label in dialog.connection_page.findChildren(QLabel)
    )
    assert "erzeugt dort niemals selbst einen Index" in visible_copy
    assert dialog.connection_page.data_root.isReadOnly()
    dialog.close()
