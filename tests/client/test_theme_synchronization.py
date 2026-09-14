from __future__ import annotations

import subprocess
import sys

import pytest
from PySide6.QtCore import QSettings, QThread

from papagui_client.composition import ClientContainer
from papagui_client.config import ClientSettings
from papagui_client.gui import theme
from papagui_client.gui.tray import ServerTrayWindow


@pytest.mark.parametrize("visible", [True, False], ids=["open", "hidden"])
def test_running_server_window_follows_appearance_saved_by_another_process(
    qapp, qtbot, tmp_path, monkeypatch, visible,
):
    settings_path = tmp_path / "appearance.ini"
    monkeypatch.setattr(
        theme, "ui_settings",
        lambda: QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    settings = theme.ui_settings()
    settings.setValue("unrelated_preference", "keep")
    settings.sync()
    theme.save_appearance(theme.AppearanceSettings("light", "#2db89d", 100, 13))

    # Exercise the real server interface without starting API requests or jobs.
    monkeypatch.setattr(ServerTrayWindow, "refresh_status", lambda self: None)
    window = ServerTrayWindow(ClientContainer(ClientSettings(data_root=tmp_path / "data")))
    qtbot.addWidget(window)
    if visible:
        window.show()
    old_palette, old_stylesheet = qapp.palette(), qapp.styleSheet()
    synchronizer = theme.ThemeSynchronizer(qapp)
    applications = []
    original_apply = theme.ThemeManager.apply

    def record_apply(manager, application):
        applications.append(QThread.currentThread())
        original_apply(manager, application)

    monkeypatch.setattr(theme.ThemeManager, "apply", record_apply)
    writer = """
import sys
from PySide6.QtCore import QSettings
from papagui_client.gui import theme
theme.ui_settings = lambda: QSettings(sys.argv[1], QSettings.Format.IniFormat)
theme.save_appearance(theme.AppearanceSettings(sys.argv[2], '#f08a3c', 110, 17))
"""
    try:
        synchronizer.start()
        for index, mode in enumerate(("dark", "light"), start=2):
            subprocess.run(
                [sys.executable, "-c", writer, str(settings_path), mode],
                check=True, capture_output=True, text=True, timeout=20,
            )
            expected = theme.build_stylesheet(mode, "#f08a3c", 110, 17)
            qtbot.waitUntil(lambda: qapp.styleSheet() == expected, timeout=2000)
            assert qapp.palette() == theme.build_palette(mode, "#f08a3c", 110)
            assert window.palette().window().color() == qapp.palette().window().color()
            assert window.isVisible() is visible
            # Repeated checks must not restyle widgets when nothing changed.
            synchronizer.refresh()
            synchronizer.refresh()
            assert len(applications) == index
        assert all(thread == qapp.thread() for thread in applications)
        settings.sync()
        assert settings.value("unrelated_preference") == "keep"
    finally:
        synchronizer.stop()
        window.shutdown()
        window.close()
        qapp.setPalette(old_palette)
        qapp.setStyleSheet(old_stylesheet)
