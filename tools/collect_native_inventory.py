"""Inventory the native files selected by PyInstaller, without publishing host paths."""
from __future__ import annotations

import argparse
import ast
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from release_metadata import ROOT, version


def binaries(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 3 and all(isinstance(item, str) for item in value) and value[2] in {"BINARY", "EXTENSION"}:
            yield value
        else:
            for item in value:
                yield from binaries(item)


def collect(analyses: list[Path], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    selected = {Path(source).resolve() for analysis in analyses
                for _, source, _ in binaries(ast.literal_eval(analysis.read_text(encoding="utf-8")))}
    owners = {}
    for dist in metadata.distributions():
        matched = []
        for file in dist.files or []:
            if file.name.lower().endswith((".dll", ".pyd", ".so", ".dylib")) or ".so." in file.name:
                path = Path(dist.locate_file(file)).resolve()
                if path in selected:
                    matched.append(path)
        if not matched:
            continue
        name = dist.metadata["Name"]
        for path in matched:
            owners[path] = f"{name}=={dist.version}"
        # Optional dependencies (for example Pillow via openpyxl) may be picked
        # up by PyInstaller even when they are not direct runtime lock entries.
        # Preserve notices only for distributions actually present in the payload.
        destination = output / "python-native" / name
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "METADATA").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        for file in dist.files or []:
            if any(word in file.name.casefold() for word in ("license", "licence", "copying", "copyright", "notice")):
                path = Path(dist.locate_file(file))
                if path.is_file():
                    filename = hashlib.sha256(str(file).encode()).hexdigest()[:16] + "-" + file.name
                    shutil.copy2(path, destination / filename)
    inventory = []
    for analysis in analyses:
        entries = list(binaries(ast.literal_eval(analysis.read_text(encoding="utf-8"))))
        if not entries:
            raise ValueError("PyInstaller analysis contains no native files")
        for name, source, kind in entries:
            path = Path(source).resolve()
            owner = owners.get(path)
            if owner is None and path.is_relative_to(Path(sys.base_prefix).resolve()):
                owner = f"Python distribution {platform.python_version()}"
            if owner is None and sys.platform == "linux":
                result = subprocess.run(["dpkg-query", "-S", str(path)], capture_output=True, text=True)
                if result.returncode == 0:
                    package = result.stdout.split(": ", 1)[0].split(":", 1)[0]
                    owner = "Debian/Ubuntu package: " + package
                    copyright_file = Path("/usr/share/doc") / package / "copyright"
                    if copyright_file.is_file():
                        shutil.copy2(copyright_file, output / (package + "-copyright.txt"))
            if owner is None:
                owner = "Platform runtime; see native-license-review.md"
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            inventory.append({"product": analysis.parent.name, "file": name.replace("\\", "/"),
                              "kind": kind, "owner": owner, "sha256": digest})
    payload = {"schema": 1, "version": version("client"), "platform": sys.platform,
               "architecture": platform.machine(), "python": platform.python_version(),
               "binaries": sorted(inventory, key=lambda item: (item["product"], item["file"]))}
    (output / "native-inventory.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(inventory)} native payload entries without absolute build paths.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/native-notices")
    args = parser.parse_args()
    collect(args.analysis, args.output)
