"""Validate release metadata and stage license files for independent packages."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ("contracts", "server", "client")
LICENSE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def version(component: str) -> str:
    source = ROOT / f"packages/{component}/src/papagui_{component}/__init__.py"
    name = "__version__"
    if component == "contracts":
        source = source.with_name("system.py")
        name = "CONTRACT_VERSION"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return str(ast.literal_eval(node.value))
    raise ValueError(f"Missing version: {component}")


def check(*, sync: bool = False) -> None:
    versions = {component: version(component) for component in COMPONENTS}
    if len(set(versions.values())) != 1:
        raise ValueError(f"Component version drift: {versions}")
    matrix = json.loads((ROOT / "packaging/client/build-matrix.json").read_text(encoding="utf-8"))
    if matrix["version"] != versions["client"]:
        raise ValueError("Build matrix version differs from client")
    for component in COMPONENTS:
        package = ROOT / "packages" / component
        metadata = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        assert metadata["project"]["license"] == "GPL-3.0-or-later", component
        assert metadata["project"]["license-files"] == list(LICENSE_FILES), component
        if component != "contracts":
            required = f"papagui-contracts=={versions['contracts']}"
            if required not in metadata["project"]["dependencies"]:
                raise ValueError(f"{component}: expected dependency {required}")
        for name in LICENSE_FILES:
            expected = (ROOT / name).read_bytes()
            target = package / name
            if sync:
                target.write_bytes(expected)
            if not target.is_file() or target.read_bytes() != expected:
                raise ValueError(f"Stale {target}: run tools/release_metadata.py --sync")
        print(f"{component}: {version(component)}, GPL-3.0-or-later")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    check(sync=args.sync)


if __name__ == "__main__":
    main()
