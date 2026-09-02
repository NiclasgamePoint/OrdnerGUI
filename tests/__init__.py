"""Hermetic process environment shared by all project tests.

Importing a ``tests.*`` module imports this package first. That makes this the
earliest reliable place to redirect package data and Qt settings before a
client test can load its configuration or create a ``QSettings`` instance.
``tests.run_ci`` supplies a private root for every test module.  Direct unittest
invocations receive a process-private temporary root here as a safe fallback.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


_temporary_directory: tempfile.TemporaryDirectory[str] | None = None


def _resolve_test_root() -> Path:
    global _temporary_directory

    configured = os.environ.get("PAPAGUI_TEST_ROOT", "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    _temporary_directory = tempfile.TemporaryDirectory(prefix="papagui-tests-")
    root = Path(_temporary_directory.name).resolve()
    os.environ["PAPAGUI_TEST_ROOT"] = str(root)
    return root


def _prepare_directories(root: Path) -> dict[str, Path]:
    directories = {
        "data": root / "data",
        "source": root / "source",
        "config": root / "config",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def _configure_qsettings(config_directory: Path) -> None:
    # Local import is intentional: all path environment variables must exist
    # before Qt is imported and allowed to resolve a platform settings backend.
    try:
        from PySide6.QtCore import QSettings
    except ModuleNotFoundError:
        # Contracts and server tests deliberately run in a Qt-free environment.
        # Client GUI test jobs install the GUI extra and take this branch.
        return

    settings_format = QSettings.Format.IniFormat
    QSettings.setDefaultFormat(settings_format)
    QSettings.setPath(
        settings_format,
        QSettings.Scope.UserScope,
        str(config_directory),
    )
    QSettings.setPath(
        settings_format,
        QSettings.Scope.SystemScope,
        str(config_directory),
    )


TEST_ROOT = _resolve_test_root()
TEST_DIRECTORIES = _prepare_directories(TEST_ROOT)

# Deliberately overwrite inherited values: a test process must never inherit a
# developer's real application data or project source directory.
os.environ["PAPAGUI_DATA_DIR"] = str(TEST_DIRECTORIES["data"])
os.environ["PAPAGUI_SOURCE_DIR"] = str(TEST_DIRECTORIES["source"])
os.environ["PAPAGUI_SETTINGS_DIR"] = str(TEST_DIRECTORIES["config"])
os.environ["XDG_CONFIG_HOME"] = str(TEST_DIRECTORIES["config"])
os.environ["APPDATA"] = str(TEST_DIRECTORIES["config"])
os.environ["LOCALAPPDATA"] = str(TEST_DIRECTORIES["config"])
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_configure_qsettings(TEST_DIRECTORIES["config"])
