from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_frozen_client import (
    FrozenArtifactError,
    executable_paths,
    validate_members,
)


def test_executable_paths_match_native_bundle_shapes(tmp_path: Path) -> None:
    assert executable_paths(tmp_path, "linux") == {
        "client": tmp_path / "papagui-client",
        "tray": tmp_path / "papagui-tray",
    }
    assert executable_paths(tmp_path, "win32") == {
        "client": tmp_path / "papagui-client.exe",
        "tray": tmp_path / "papagui-tray.exe",
    }
    macos = executable_paths(tmp_path, "darwin")
    assert macos["client"].as_posix().endswith("PapaGUI Client.app/Contents/MacOS/papagui-client")
    assert macos["tray"].as_posix().endswith(
        "PapaGUI Tray.app/Contents/MacOS/papagui-tray"
    )
    assert set(macos) == {"client", "tray"}


def test_frozen_member_boundaries_require_products_and_reject_writers() -> None:
    valid = {
        "papagui_client.entrypoints.client",
        "papagui_contracts.generations",
        "PySide6.QtCore",
    }
    validate_members("client", valid)

    for forbidden in (
        "papagui_server.api.app",
        "app.core.index_manager",
        "papagui_client.index_writer",
    ):
        with pytest.raises(FrozenArtifactError):
            validate_members("client", {*valid, forbidden})

    with pytest.raises(FrozenArtifactError):
        validate_members("client", {"papagui_client.entrypoints.client"})
