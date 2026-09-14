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
    from packaging.requirements import Requirement

    owners = {}
    for line in (ROOT / "packages/client/requirements-lock.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        dist = metadata.distribution(requirement.name)
        for file in dist.files or []:
            owners[Path(dist.locate_file(file)).resolve()] = f"{dist.metadata['Name']}=={dist.version}"
    output.mkdir(parents=True, exist_ok=True)
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
