"""Launch the independent tray process when the desktop client starts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


class TrayProcessLauncher:
    def command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable)
            if sys.platform == "darwin":
                bundle = self._macos_bundle(executable)
                if bundle is not None:
                    tray = (
                        bundle.parent
                        / "PapaGUI Tray.app"
                        / "Contents"
                        / "MacOS"
                        / "papagui-tray"
                    )
                    return [str(tray), "--background"]
            suffix = ".exe" if os.name == "nt" else ""
            sibling = executable.with_name(f"papagui-tray{suffix}")
            return [str(sibling), "--background"]
        return [sys.executable, "-m", "papagui_client.entrypoints.tray", "--background"]

    @staticmethod
    def _macos_bundle(executable: Path) -> Path | None:
        """Return the enclosing app bundle for a standard macOS executable."""
        macos = executable.parent
        contents = macos.parent
        bundle = contents.parent
        if macos.name == "MacOS" and contents.name == "Contents" and bundle.suffix == ".app":
            return bundle
        return None

    def start(self) -> None:
        options: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            options["start_new_session"] = True
        subprocess.Popen(self.command(), **options)
