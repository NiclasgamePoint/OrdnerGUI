"""Collect installed, locked dependency notices without scanning user data.

This inventory is not a complete native-library SBOM or a source-code offer.
Generate it separately in each target's build environment.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import sys

from packaging.requirements import Requirement

from release_metadata import ROOT, LICENSE_FILES, version


def collect(component: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    for name in LICENSE_FILES:
        shutil.copy2(ROOT / name, output / name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        import sysconfig

        python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python LICENSE.txt missing; supply the interpreter's license")
    shutil.copy2(python_license, output / "PYTHON-LICENSE.txt")
    requirements = ROOT / f"packages/{component}/requirements-lock.txt"
    packages = []
    locked = requirements.read_text(encoding="utf-8").splitlines()
    if component == "client":
        locked.extend(
            line for line in (ROOT / "packaging/client/requirements-build-lock.txt")
            .read_text(encoding="utf-8").splitlines() if line.startswith("pyinstaller==")
        )
    for raw in locked:
        if not raw.strip() or raw.startswith("#"):
            continue
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        dist = metadata.distribution(requirement.name)
        if not requirement.specifier.contains(dist.version):
            raise RuntimeError(f"Installed {requirement.name}=={dist.version} differs from lock")
        destination = output / requirement.name
        destination.mkdir()
        (destination / "METADATA").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        notices = []
        for file in dist.files or []:
            if not any(word in file.name.casefold() for word in ("license", "copying", "copyright", "notice")):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file():
                # Hash the relative path to avoid colliding license filenames.
                name = hashlib.sha256(str(file).encode()).hexdigest()[:12] + "-" + file.name
                shutil.copy2(source, destination / name)
                notices.append({"original": str(file), "file": name})
        packages.append({
            "name": dist.metadata["Name"], "version": dist.version,
            "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
            "project_urls": dist.metadata.get_all("Project-URL", []),
            "notices": notices,
        })
    (output / "dependency-inventory.json").write_text(json.dumps({
        "component": component, "version": version(component),
        "python": platform.python_version(), "platform": sys.platform,
        "architecture": platform.machine(), "packages": packages,
        "limitations": [
            "Python lock inventory only; not a complete native-library SBOM.",
            "Audit Qt/PDFium, Python and OS libraries and archive corresponding sources before release.",
        ],
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("client", "server"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect(args.component, args.output)
