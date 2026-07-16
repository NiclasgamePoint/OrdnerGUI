from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

try:
    import olefile
except Exception:  # pragma: no cover - optional dependency fallback
    olefile = None


class DocumentConverter:
    """Isolated, optional LibreOffice/catdoc integration for legacy documents."""

    def __init__(self):
        self._temporary_directory: TemporaryDirectory | None = None
        self._last_operation = {
            "converted": False,
            "tool": "Direkt",
            "action": "none",
        }

    def reset_last_operation(self):
        self._last_operation = {
            "converted": False,
            "tool": "Direkt",
            "action": "none",
        }

    def get_last_operation(self) -> dict[str, object]:
        return dict(self._last_operation)

    def _set_last_operation(self, *, converted: bool, tool: str, action: str):
        self._last_operation = {
            "converted": converted,
            "tool": tool,
            "action": action,
        }

    def extract_legacy_doc(self, path: Path) -> str:
        executable = shutil.which("catdoc") or shutil.which("antiword")
        if executable is None:
            text, tool = self._extract_legacy_doc_python(path)
            if text:
                self._set_last_operation(converted=False, tool=tool, action="extract_doc")
                return text
            self._set_last_operation(converted=False, tool="Nicht verfügbar", action="extract_doc")
            raise RuntimeError("Für .doc wurde kein externer Konverter gefunden")

        result = subprocess.run(
            [executable, str(path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            self._set_last_operation(
                converted=False,
                tool=Path(executable).name,
                action="extract_doc",
            )
            return result.stdout

        text, tool = self._extract_legacy_doc_python(path)
        if text:
            self._set_last_operation(converted=False, tool=tool, action="extract_doc")
            return text
        self._set_last_operation(converted=False, tool=Path(executable).name, action="extract_doc")
        raise RuntimeError(result.stderr.strip() or "Konvertierung ist fehlgeschlagen")

    def _extract_legacy_doc_python(self, path: Path) -> tuple[str, str]:
        if not path.exists():
            return "", "Python-Fallback"

        # Try OLE streams first (higher signal), then raw binary fallback.
        if olefile is not None and olefile.isOleFile(str(path)):
            try:
                with olefile.OleFileIO(str(path)) as ole:
                    chunks: list[str] = []
                    for stream in ole.listdir(streams=True, storages=False):
                        name = stream[-1] if stream else ""
                        if name not in {"WordDocument", "1Table", "0Table", "Data"}:
                            continue
                        data = ole.openstream(stream).read()
                        text = self._extract_strings_from_bytes(data)
                        if text:
                            chunks.append(text)
                    merged = "\n".join(chunks).strip()
                    if merged:
                        return merged, "Python (olefile)"
            except Exception:
                pass

        try:
            data = path.read_bytes()
        except OSError:
            return "", "Python-Fallback"
        return self._extract_strings_from_bytes(data), "Python (binär)"

    @staticmethod
    def _extract_strings_from_bytes(data: bytes) -> str:
        if not data:
            return ""

        ascii_chunks = [
            match.group().decode("cp1252", errors="ignore")
            for match in re.finditer(rb"[\x20-\x7E\x80-\xFF\t\r\n]{5,}", data)
        ]
        utf16_chunks = [
            match.group().decode("utf-16-le", errors="ignore")
            for match in re.finditer(rb"(?:[\x09\x0A\x0D\x20-\x7E\x80-\xFF]\x00){5,}", data)
        ]

        seen = set()
        lines: list[str] = []
        for chunk in utf16_chunks + ascii_chunks:
            for raw_line in chunk.splitlines():
                line = re.sub(r"\s+", " ", raw_line).strip()
                if len(line) < 3:
                    continue
                if line in seen:
                    continue
                # Drop mostly-symbol noise from binary payloads.
                alpha = sum(char.isalnum() for char in line)
                if alpha < max(2, len(line) // 6):
                    continue
                seen.add(line)
                lines.append(line)
                if sum(len(item) for item in lines) >= 2_000_000:
                    return "\n".join(lines)
        return "\n".join(lines)

    def convert(self, path: Path, target_extension: str) -> Path:
        path = path.resolve()
        target_extension = target_extension.lower().lstrip(".")
        if converted := self._convert_with_libreoffice(path, target_extension):
            self._set_last_operation(converted=True, tool="LibreOffice", action="convert")
            return converted
        if converted := self._convert_with_ms_office(path, target_extension):
            self._set_last_operation(converted=True, tool="MS Office", action="convert")
            return converted
        self._set_last_operation(converted=False, tool="Nicht verfügbar", action="convert")
        raise RuntimeError("Kein geeigneter Konverter gefunden (weder LibreOffice noch MS Office)")

    def _convert_with_libreoffice(self, path: Path, target_extension: str) -> Path | None:
        executable = shutil.which("libreoffice") or shutil.which("soffice")
        if executable is None:
            return None
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
            self.cleanup()
            return None
        return candidates[0]

    def _convert_with_ms_office(self, path: Path, target_extension: str) -> Path | None:
        if not self._can_use_ms_office():
            return None

        source_extension = path.suffix.lower().lstrip(".")
        self.cleanup()
        self._temporary_directory = TemporaryDirectory(prefix="papagui-viewer-")
        output_dir = Path(self._temporary_directory.name)
        output_path = output_dir / f"{path.stem}.{target_extension}"

        if source_extension in {"xls", "xlsx"} and target_extension == "xlsx":
            return self._ms_excel_convert(path, output_path)
        if source_extension in {"doc", "docx"} and target_extension in {"pdf", "txt"}:
            return self._ms_word_convert(path, output_path, target_extension)

        self.cleanup()
        return None

    def _can_use_ms_office(self) -> bool:
        if sys.platform != "win32":
            return False
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return False
        script = (
            "$ErrorActionPreference='Stop';"
            "try {$x=New-Object -ComObject Excel.Application; $x.Quit(); 'ok'} catch {'no'}"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return False
        return result.returncode == 0 and "ok" in result.stdout.lower()

    def _ms_excel_convert(self, source: Path, target: Path) -> Path | None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return None
        source_path = str(source).replace("'", "''")
        target_path = str(target).replace("'", "''")
        script = (
            "$ErrorActionPreference='Stop';"
            "$excel=New-Object -ComObject Excel.Application;"
            "$excel.Visible=$false;"
            "$excel.DisplayAlerts=$false;"
            f"$wb=$excel.Workbooks.Open('{source_path}');"
            f"$wb.SaveAs('{target_path}', 51);"
            "$wb.Close($false);"
            "$excel.Quit();"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:
            self.cleanup()
            return None
        if result.returncode != 0 or not target.exists():
            self.cleanup()
            return None
        return target

    def _ms_word_convert(self, source: Path, target: Path, target_extension: str) -> Path | None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return None
        source_path = str(source).replace("'", "''")
        target_path = str(target).replace("'", "''")
        file_format = "17" if target_extension == "pdf" else "2"
        script = (
            "$ErrorActionPreference='Stop';"
            "$word=New-Object -ComObject Word.Application;"
            "$word.Visible=$false;"
            "$word.DisplayAlerts=0;"
            f"$doc=$word.Documents.Open('{source_path}', $false, $true);"
            f"$doc.SaveAs([ref]'{target_path}', [ref]{file_format});"
            "$doc.Close($false);"
            "$word.Quit();"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:
            self.cleanup()
            return None
        if result.returncode != 0 or not target.exists():
            self.cleanup()
            return None
        return target

    def cleanup(self):
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None
