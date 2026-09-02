"""Preview-only conversion of legacy Office documents.

All writes are confined to a disposable working directory or the injected
client preview cache.  The source path is treated as immutable.
"""

from __future__ import annotations

import hashlib
from itertools import chain
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from papagui_client.config import default_data_root

from .models import ConversionOutcome
from .ports import CancelCheck, CommandRunner, ExecutableResolver
from .processes import PollingCommandRunner
from .tools import DocumentToolResolver


class DocumentPreviewConverter:
    """Render documents for display without writing to the source tree."""

    def __init__(
        self,
        cache_root: Path | None = None,
        *,
        tools: ExecutableResolver | None = None,
        commands: CommandRunner | None = None,
    ) -> None:
        self.cache_root = cache_root or default_data_root() / "preview-cache" / "word"
        self._tools = tools or DocumentToolResolver()
        self._commands = commands or PollingCommandRunner()
        self._temporary_directory: TemporaryDirectory[str] | None = None

    def extract_legacy_doc(
        self,
        path: Path,
        should_cancel: CancelCheck | None = None,
    ) -> ConversionOutcome:
        executable = self._resolve_first("catdoc", "antiword")
        if executable is not None:
            result = self._commands.run(
                [str(executable), str(path)],
                timeout=30,
                should_cancel=should_cancel,
            )
            if result.returncode == 0 and result.stdout.strip():
                return ConversionOutcome(
                    action="extract_doc",
                    converted=False,
                    tool=executable.name,
                    text=result.stdout,
                )

        text, tool = self._extract_legacy_doc_python(path)
        if text:
            return ConversionOutcome(
                action="extract_doc",
                converted=False,
                tool=tool,
                text=text,
            )
        failed_tool = executable.name if executable is not None else "Nicht verfügbar"
        raise RuntimeError(
            f"Inhalt der DOC-Datei konnte nicht extrahiert werden ({failed_tool})"
        )

    def convert(
        self,
        path: Path,
        target_extension: str,
        should_cancel: CancelCheck | None = None,
    ) -> ConversionOutcome:
        source = path.resolve()
        extension = target_extension.lower().lstrip(".")
        converted = self._convert_with_libreoffice(source, extension, should_cancel)
        if converted is not None:
            return ConversionOutcome("convert", "LibreOffice", True, path=converted)
        self._raise_if_cancelled(should_cancel)
        converted = self._convert_with_ms_office(source, extension, should_cancel)
        if converted is not None:
            return ConversionOutcome("convert", "MS Office", True, path=converted)
        raise RuntimeError(
            "Kein geeigneter Konverter gefunden (weder LibreOffice noch MS Office)"
        )

    def convert_word_to_pdf(
        self,
        path: Path,
        should_cancel: CancelCheck | None = None,
        *,
        prefer_ms_office: bool = True,
    ) -> ConversionOutcome:
        source = path.resolve()
        if source.suffix.lower() not in {".doc", ".docx"}:
            raise ValueError("Nur Word-Dokumente können als PDF-Vorschau gerendert werden")

        cache_path = self._word_preview_cache_path(source)
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return ConversionOutcome("word_to_pdf", "Cache", True, path=cache_path)

        backends = (
            ("MS Office", self._convert_with_ms_office),
            ("LibreOffice", self._convert_with_libreoffice),
        )
        if not (prefer_ms_office and sys.platform == "win32"):
            backends = tuple(reversed(backends))
        for tool, backend in backends:
            # MS Office is meaningful only on Windows.  The backend itself also
            # checks this, keeping the sequencing easy to test.
            converted = backend(source, "pdf", should_cancel)
            if converted is not None:
                cached = self._store_word_preview_cache(converted, cache_path)
                return ConversionOutcome("word_to_pdf", tool, True, path=cached)
            self._raise_if_cancelled(should_cancel)
        raise RuntimeError(
            "Kein geeigneter Word-Konverter gefunden (weder MS Office noch LibreOffice)"
        )

    def _extract_legacy_doc_python(self, path: Path) -> tuple[str, str]:
        if not path.is_file():
            return "", "Python-Fallback"

        try:
            import olefile
        except ImportError:  # pragma: no cover - depends on optional install
            olefile = None
        if olefile is not None:
            try:
                is_ole = olefile.isOleFile(str(path))
            except Exception:
                is_ole = False
            if is_ole:
                try:
                    with olefile.OleFileIO(str(path)) as document:
                        chunks: list[str] = []
                        for stream in document.listdir(streams=True, storages=False):
                            name = stream[-1] if stream else ""
                            if name not in {"WordDocument", "1Table", "0Table", "Data"}:
                                continue
                            text = self._extract_strings_from_bytes(
                                document.openstream(stream).read()
                            )
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
    def _extract_strings_from_bytes(data: bytes, maximum: int = 2_000_000) -> str:
        if not data:
            return ""
        ascii_chunks = (
            match.group().decode("cp1252", errors="ignore")
            for match in re.finditer(rb"[\x20-\x7E\x80-\xFF\t\r\n]{5,}", data)
        )
        utf16_chunks = (
            match.group().decode("utf-16-le", errors="ignore")
            for match in re.finditer(
                rb"(?:[\x09\x0A\x0D\x20-\x7E\x80-\xFF]\x00){5,}", data
            )
        )
        seen: set[str] = set()
        lines: list[str] = []
        extracted_length = 0
        for chunk in chain(utf16_chunks, ascii_chunks):
            for raw_line in chunk.splitlines():
                line = re.sub(r"\s+", " ", raw_line).strip()
                if len(line) < 3 or line in seen:
                    continue
                alphanumeric = sum(character.isalnum() for character in line)
                if alphanumeric < max(2, len(line) // 6):
                    continue
                seen.add(line)
                remaining = maximum - extracted_length
                if remaining <= 0:
                    return "\n".join(lines)
                lines.append(line[:remaining])
                extracted_length += len(lines[-1])
                if extracted_length >= maximum:
                    return "\n".join(lines)
        return "\n".join(lines)

    def _convert_with_libreoffice(
        self,
        path: Path,
        target_extension: str,
        should_cancel: CancelCheck | None,
    ) -> Path | None:
        executable = self._resolve_first("libreoffice", "soffice")
        if executable is None:
            return None
        output = self._new_working_directory()
        profile = output / "profile"
        runtime = output / "runtime"
        config = output / "config"
        cache = output / "cache"
        profile.mkdir()
        runtime.mkdir(mode=0o700)
        config.mkdir()
        cache.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "XDG_RUNTIME_DIR": str(runtime),
                "XDG_CONFIG_HOME": str(config),
                "XDG_CACHE_HOME": str(cache),
                "SAL_USE_VCLPLUGIN": "svp",
            }
        )
        result = self._commands.run(
            [
                str(executable),
                "--headless",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                target_extension,
                "--outdir",
                str(output),
                str(path),
            ],
            timeout=60,
            environment=environment,
            should_cancel=should_cancel,
        )
        candidates = tuple(output.glob(f"*.{target_extension}"))
        if result.returncode != 0 or not candidates:
            self.cleanup()
            return None
        return candidates[0]

    def _convert_with_ms_office(
        self,
        path: Path,
        target_extension: str,
        should_cancel: CancelCheck | None,
    ) -> Path | None:
        source_extension = path.suffix.lower().lstrip(".")
        if sys.platform != "win32":
            return None
        if source_extension in {"doc", "docx"}:
            if not self._can_use_ms_word(should_cancel):
                return None
        elif source_extension in {"xls", "xlsx"}:
            if not self._can_use_ms_excel(should_cancel):
                return None
        else:
            return None

        output = self._new_working_directory()
        target = output / f"{path.stem}.{target_extension}"
        if source_extension in {"xls", "xlsx"} and target_extension == "xlsx":
            return self._run_excel_conversion(path, target, should_cancel)
        if source_extension in {"doc", "docx"} and target_extension in {"pdf", "txt"}:
            return self._run_word_conversion(
                path, target, target_extension, should_cancel
            )
        self.cleanup()
        return None

    def _can_use_ms_excel(self, should_cancel: CancelCheck | None) -> bool:
        return self._can_create_com_object("Excel.Application", should_cancel)

    def _can_use_ms_word(self, should_cancel: CancelCheck | None) -> bool:
        return self._can_create_com_object("Word.Application", should_cancel)

    def _can_create_com_object(
        self, object_name: str, should_cancel: CancelCheck | None
    ) -> bool:
        if sys.platform != "win32":
            return False
        powershell = self._resolve_first("powershell", "pwsh")
        if powershell is None:
            return False
        variable = "excel" if object_name.startswith("Excel") else "word"
        script = (
            "$ErrorActionPreference='Stop';"
            f"try {{${variable}=New-Object -ComObject {object_name};"
            f"${variable}.Quit(); 'ok'}} catch {{'no'}}"
        )
        try:
            result = self._commands.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                timeout=10,
                should_cancel=should_cancel,
            )
        except InterruptedError:
            raise
        except Exception:
            return False
        return result.returncode == 0 and "ok" in result.stdout.casefold()

    def _run_excel_conversion(
        self,
        source: Path,
        target: Path,
        should_cancel: CancelCheck | None,
    ) -> Path | None:
        powershell = self._resolve_first("powershell", "pwsh")
        if powershell is None:
            return None
        source_path = self._powershell_literal(source)
        target_path = self._powershell_literal(target)
        script = (
            "$ErrorActionPreference='Stop';"
            "$excel=$null;$book=$null;try {"
            "$excel=New-Object -ComObject Excel.Application;"
            "$excel.Visible=$false;$excel.DisplayAlerts=$false;"
            f"$book=$excel.Workbooks.Open('{source_path}');"
            f"$book.SaveAs('{target_path}', 51);"
            "} finally {"
            "if ($book -ne $null) {$book.Close($false)};"
            "if ($excel -ne $null) {$excel.Quit()};}"
        )
        return self._run_powershell_conversion(
            powershell, script, target, should_cancel
        )

    def _run_word_conversion(
        self,
        source: Path,
        target: Path,
        target_extension: str,
        should_cancel: CancelCheck | None,
    ) -> Path | None:
        powershell = self._resolve_first("powershell", "pwsh")
        if powershell is None:
            return None
        source_path = self._powershell_literal(source)
        target_path = self._powershell_literal(target)
        file_format = "17" if target_extension == "pdf" else "2"
        script = (
            "$ErrorActionPreference='Stop';"
            "$word=$null;$doc=$null;try {"
            "$word=New-Object -ComObject Word.Application;"
            "$word.Visible=$false;$word.DisplayAlerts=0;"
            f"$doc=$word.Documents.Open('{source_path}', $false, $true);"
            f"$doc.SaveAs([ref]'{target_path}', [ref]{file_format});"
            "} finally {"
            "if ($doc -ne $null) {$doc.Close($false)};"
            "if ($word -ne $null) {$word.Quit()};}"
        )
        return self._run_powershell_conversion(
            powershell, script, target, should_cancel
        )

    def _run_powershell_conversion(
        self,
        powershell: Path,
        script: str,
        target: Path,
        should_cancel: CancelCheck | None,
    ) -> Path | None:
        try:
            result = self._commands.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                timeout=60,
                should_cancel=should_cancel,
            )
        except InterruptedError:
            self.cleanup()
            raise
        except Exception:
            self.cleanup()
            return None
        if result.returncode != 0 or not target.is_file():
            self.cleanup()
            return None
        return target

    @staticmethod
    def _powershell_literal(path: Path) -> str:
        return str(path).replace("'", "''")

    def _resolve_first(self, *names: str) -> Path | None:
        for name in names:
            if executable := self._tools.resolve(name):
                return executable
        return None

    def _new_working_directory(self) -> Path:
        self.cleanup()
        self._temporary_directory = TemporaryDirectory(prefix="papagui-viewer-")
        return Path(self._temporary_directory.name)

    def _word_preview_cache_path(self, path: Path) -> Path:
        stat = path.stat()
        payload = "|".join(
            (str(path.resolve()), str(stat.st_size), str(stat.st_mtime_ns), path.suffix.lower())
        )
        digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
        return self.cache_root / f"{digest}.pdf"

    @staticmethod
    def _store_word_preview_cache(source: Path, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    @staticmethod
    def _raise_if_cancelled(should_cancel: CancelCheck | None) -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Konvertierung wurde abgebrochen")

    def clear_cache(self) -> None:
        shutil.rmtree(self.cache_root, ignore_errors=True)

    def cleanup(self) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def __enter__(self) -> DocumentPreviewConverter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()
