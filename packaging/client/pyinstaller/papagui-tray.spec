# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

project_root = Path(SPEC).resolve().parents[3]
client_src = project_root / "packages" / "client" / "src"
contracts_src = project_root / "packages" / "contracts" / "src"
sys.path.insert(0, str(client_src))
from papagui_client import __version__ as client_version

analysis = Analysis(
    [str(client_src / "papagui_client" / "entrypoints" / "tray.py")],
    pathex=[str(client_src), str(contracts_src)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=[
        "papagui_server",
        "app",
        "index_job",
        "index_manager",
        "index_writer",
        "customer_recognition",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="papagui-tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

if sys.platform == "darwin":
    application = BUNDLE(
        executable,
        name="PapaGUI Tray.app",
        bundle_identifier="de.papagui.tray",
        info_plist={
            "CFBundleDisplayName": "PapaGUI Tray",
            "CFBundleShortVersionString": client_version,
            "CFBundleVersion": client_version,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
