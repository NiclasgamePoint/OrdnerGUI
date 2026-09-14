from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QDialog, QWidget
import pytest

from papagui_client.gui.icons import ICON_DIRECTORY, application_icon


@pytest.mark.parametrize("role", ("client", "server"))
def test_icons_render_at_tray_and_high_dpi_sizes_and_inherit_in_dialogs(qtbot, qapp, role):
    icon = application_icon(role)
    assert not icon.isNull()
    for size in (16, 20, 24, 32, 48, 64, 128, 256, 512):
        pixmap = icon.pixmap(QSize(size, size))
        assert pixmap.size() == QSize(size, size)
        pixels = pixmap.toImage()
        assert pixels.hasAlphaChannel()
        assert pixels.pixelColor(0, 0).alpha() == 0
        # Downsampling antialiased artwork may leave a few alpha levels below opaque.
        assert pixels.pixelColor(size // 2, size // 2).alpha() >= 245

    previous = qapp.windowIcon()
    try:
        qapp.setWindowIcon(icon)
        parent = QWidget()
        dialog = QDialog(parent)
        standalone = QDialog()
        for widget in (parent, dialog, standalone):
            qtbot.addWidget(widget)
            assert widget.windowIcon().cacheKey() == icon.cacheKey()
    finally:
        qapp.setWindowIcon(previous)


def test_client_and_server_have_distinct_artwork(qapp):
    client = application_icon("client").pixmap(32, 32).toImage()
    server = application_icon("server").pixmap(32, 32).toImage()
    assert client != server


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows shell integration")
@pytest.mark.parametrize("role,expected", (("client", "de.papagui.client"), ("server", "de.papagui.tray")))
def test_windows_shell_receives_separate_app_ids_without_changing_test_process(role, expected):
    script = """
import ctypes
import sys
from papagui_client.gui.icons import set_process_identity
set_process_identity(sys.argv[1])
shell = ctypes.WinDLL('shell32')
get_id = shell.GetCurrentProcessExplicitAppUserModelID
get_id.argtypes = [ctypes.POINTER(ctypes.c_wchar_p)]
get_id.restype = ctypes.c_long
value = ctypes.c_wchar_p()
assert get_id(ctypes.byref(value)) == 0
print(value.value)
free = ctypes.WinDLL('ole32').CoTaskMemFree
free.argtypes = [ctypes.c_void_p]
free(value)
"""
    environment = os.environ.copy()
    source = str(ICON_DIRECTORY.parents[2])
    environment["PYTHONPATH"] = os.pathsep.join((source, environment.get("PYTHONPATH", "")))
    result = subprocess.run(
        [sys.executable, "-c", script, role],
        env=environment,
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.stdout.strip() == expected
