"""Cross-platform resolution of optional document preview tools."""

from __future__ import annotations

from pathlib import Path
import platform
import shutil
import sys
from typing import Callable


class DocumentToolResolver:
    """Prefer client-bundled tools and fall back to the user's ``PATH``.

    This is intentionally a viewer concern.  Despite originating in the old
    index module, it does not know about index jobs, server state, or writers.
    """

    def __init__(
        self,
        bundle_root: Path | None = None,
        *,
        path_lookup: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.bundle_root = bundle_root or Path(__file__).resolve().parent / "vendor" / "poppler"
        self._path_lookup = path_lookup

    @staticmethod
    def platform_tag(
        operating_system: str | None = None,
        machine: str | None = None,
    ) -> str:
        operating_system = operating_system or sys.platform
        machine = (machine or platform.machine()).lower()
        if operating_system == "win32":
            return "windows-x64"
        if operating_system == "darwin":
            return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-x64"
        return "linux-x64"

    def resolve(self, name: str) -> Path | None:
        if not name or Path(name).name != name:
            raise ValueError("tool name must be a bare executable name")
        executable = f"{name}.exe" if sys.platform == "win32" else name
        packaged = self.bundle_root / self.platform_tag() / "bin" / executable
        if packaged.is_file():
            return packaged
        found = self._path_lookup(name)
        return Path(found) if found else None
