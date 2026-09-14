"""Exercise a Windows installer in a disposable directory (not an existing install)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from check_frozen_client import validate


def check(directory: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("This smoke test requires Windows")
    # Inno uses a stable per-user uninstall key. Never overwrite an existing install.
    import winreg

    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{A789C903-AF7D-40AB-B8E3-74D25234D415}_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
            raise RuntimeError("PapaGUI is already installed; run smoke tests in a fresh account")
    except FileNotFoundError:
        pass
    installers = list(directory.glob("*-windows-x64-setup-unsigned.exe"))
    if len(installers) != 1:
        raise RuntimeError("Expected exactly one Windows installer")
    with TemporaryDirectory(prefix="papagui-install-smoke-") as temporary:
        install = Path(temporary) / "PapaGUI"
        command = [str(installers[0].resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                   "/NORESTART", "/NOICONS", f"/DIR={install}"]
        try:
            subprocess.run(command, check=True, timeout=180)
            validate(install)
            if not (install / "licenses/LICENSE").is_file():
                raise RuntimeError("Installed license missing")
            # Reinstall over the same directory to exercise the upgrade path.
            subprocess.run(command, check=True, timeout=180)
            validate(install)
        finally:
            uninstall = install / "unins000.exe"
            if uninstall.is_file():
                subprocess.run([str(uninstall), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                               check=True, timeout=180)
        if (install / "papagui-client.exe").exists() or (install / "papagui-tray.exe").exists():
            raise RuntimeError("Uninstaller left application executables behind")
    print("Windows install, upgrade, launch and uninstall checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    check(args.directory)
