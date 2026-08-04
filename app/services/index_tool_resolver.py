from __future__ import annotations

from pathlib import Path
import platform
import shutil
import sys


class IndexToolResolver:
    """Resolve packaged index tools before development-machine PATH tools."""

    def __init__(self, bundle_root: Path | None = None):
        self.bundle_root = bundle_root or Path(__file__).resolve().parents[1] / "vendor" / "poppler"

    @staticmethod
    def platform_tag() -> str:
        machine = platform.machine().lower()
        if sys.platform == "win32":
            return "windows-x64"
        if sys.platform == "darwin":
            return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-x64"
        return "linux-x64"

    def resolve(self, name: str) -> Path | None:
        executable = f"{name}.exe" if sys.platform == "win32" else name
        packaged = self.bundle_root / self.platform_tag() / "bin" / executable
        if packaged.is_file():
            return packaged
        found = shutil.which(name)
        return Path(found) if found else None
