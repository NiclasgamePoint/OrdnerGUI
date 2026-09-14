"""Generate PyInstaller PE metadata from the package version, without Qt imports."""

from __future__ import annotations

from pathlib import Path
import re


def write_version_info(output: Path, version: str, executable: str) -> Path:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Windows package version must have three numeric components")
    numbers = tuple(int(part) for part in version.split(".")) + (0,)
    if any(part > 65535 for part in numbers):
        raise ValueError("Windows version component exceeds 65535")
    descriptions = {"papagui-client": "PapaGUI Client", "papagui-tray": "PapaGUI Indexserver Control"}
    if executable not in descriptions:
        raise ValueError("Unknown PapaGUI Windows executable")
    fields = {
        "CompanyName": "PapaGUI contributors",
        "FileDescription": descriptions[executable],
        "FileVersion": version,
        "InternalName": executable,
        "LegalCopyright": "Copyright (C) 2026 PapaGUI contributors",
        "OriginalFilename": executable + ".exe",
        "ProductName": "PapaGUI",
        "ProductVersion": version,
    }
    strings = ",\n".join(f"        StringStruct({key!r}, {value!r})" for key, value in fields.items())
    content = f"""# UTF-8; generated from the client package version.
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r},
                    mask=0x3f, flags=0, OS=0x40004, fileType=1,
                    subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
{strings}
      ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    output.mkdir(parents=True, exist_ok=True)
    path = output / (executable + "-version.txt")
    path.write_text(content, encoding="utf-8")
    return path
