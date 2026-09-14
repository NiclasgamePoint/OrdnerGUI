"""Packaged application artwork and native desktop identities."""

from __future__ import annotations

import ctypes
from pathlib import Path
import sys
from typing import Literal

from PySide6.QtGui import QIcon


ApplicationRole = Literal["client", "server"]
APPLICATION_IDS = {"client": "de.papagui.client", "server": "de.papagui.tray"}
ICON_DIRECTORY = Path(__file__).resolve().parents[1] / "resources" / "icons"


def application_icon(role: ApplicationRole) -> QIcon:
    """Use embedded small sizes plus the large PNG for high-DPI desktops."""
    icon = QIcon(str(ICON_DIRECTORY / f"papagui-{role}.ico"))
    icon.addFile(str(ICON_DIRECTORY / f"papagui-{role}.png"))
    return icon


def set_process_identity(role: ApplicationRole) -> None:
    """Call before creating QApplication to avoid Python's Windows taskbar group."""
    if sys.platform != "win32":
        return
    try:
        set_app_id = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id(APPLICATION_IDS[role])
    except OSError:
        # Shell integration is optional; a failure must not prevent GUI startup.
        pass
