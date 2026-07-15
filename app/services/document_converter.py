from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


class DocumentConverter:
    """Isolated, optional LibreOffice/catdoc integration for legacy documents."""

    def __init__(self):
        self._temporary_directory: TemporaryDirectory | None = None

    def extract_legacy_doc(self, path: Path) -> str:
        executable = shutil.which("catdoc") or shutil.which("antiword")
        if executable is None:
            raise RuntimeError("Für .doc wurde weder catdoc noch antiword gefunden")
        result = subprocess.run(
            [executable, str(path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Konvertierung ist fehlgeschlagen")
        return result.stdout

    def convert(self, path: Path, target_extension: str) -> Path:
        executable = shutil.which("libreoffice") or shutil.which("soffice")
        if executable is None:
            raise RuntimeError("LibreOffice wurde nicht gefunden")
        self.cleanup()
        self._temporary_directory = TemporaryDirectory(prefix="papagui-viewer-")
        output = Path(self._temporary_directory.name)
        profile = output / "profile"
        runtime = output / "runtime"
        config = output / "config"
        cache = output / "cache"
        profile.mkdir()
        runtime.mkdir(mode=0o700)
        config.mkdir()
        cache.mkdir()
        environment = os.environ.copy()
        environment.update({
            "XDG_RUNTIME_DIR": str(runtime),
            "XDG_CONFIG_HOME": str(config),
            "XDG_CACHE_HOME": str(cache),
            "SAL_USE_VCLPLUGIN": "svp",
        })
        result = subprocess.run(
            [
                executable,
                "--headless",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                target_extension,
                "--outdir",
                str(output),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        candidates = list(output.glob(f"*.{target_extension}"))
        if result.returncode != 0 or not candidates:
            message = (result.stderr or result.stdout).strip() or "LibreOffice-Konvertierung fehlgeschlagen"
            self.cleanup()
            raise RuntimeError(message)
        return candidates[0]

    def cleanup(self):
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None
