# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

project_root = Path(SPEC).resolve().parents[3]
client_src = project_root / "packages" / "client" / "src"
contracts_src = project_root / "packages" / "contracts" / "src"
icons = client_src / "papagui_client" / "resources" / "icons"
sys.path.insert(0, str(client_src))
from papagui_client import __version__ as client_version

analysis = Analysis(
    [str(client_src / "papagui_client" / "entrypoints" / "client.py")],
    pathex=[str(client_src), str(contracts_src)],
    binaries=[],
    datas=[(str(icons), "papagui_client/resources/icons")],
    hiddenimports=["openpyxl", "xlrd", "docx", "olefile"],
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
    name="papagui-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icons / "papagui-client.ico") if sys.platform == "win32" else None,
)

# A plain Mach-O executable is awkward to launch and does not behave like a
# native GUI application in Finder.  PyInstaller's BUNDLE target is macOS-only;
# Linux and Windows intentionally keep the normal executable target above.
if sys.platform == "darwin":
    application = BUNDLE(
        executable,
        name="PapaGUI Client.app",
        icon=str(icons / "papagui-client.icns"),
        bundle_identifier="de.papagui.client",
        info_plist={
            "CFBundleDisplayName": "PapaGUI Client",
            "CFBundleShortVersionString": client_version,
            "CFBundleVersion": client_version,
            "NSHighResolutionCapable": True,
        },
    )
