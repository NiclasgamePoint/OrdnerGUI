#!/usr/bin/env python3
"""Inspect and minimally launch frozen PapaGUI client products."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


class FrozenArtifactError(RuntimeError):
    """A native client artifact is missing, contaminated, or cannot start."""


def executable_paths(dist: Path, platform: str = sys.platform) -> dict[str, Path]:
    if platform == "darwin":
        return {
            "client": dist / "PapaGUI Client.app" / "Contents" / "MacOS" / "papagui-client",
            "tray": dist / "PapaGUI Tray.app" / "Contents" / "MacOS" / "papagui-tray",
        }
    suffix = ".exe" if platform == "win32" else ""
    return {
        "client": dist / f"papagui-client{suffix}",
        "tray": dist / f"papagui-tray{suffix}",
    }


def archive_members(executable: Path) -> set[str]:
    try:
        from PyInstaller.archive.readers import CArchiveReader

        archive = CArchiveReader(str(executable))
    except Exception as exc:
        raise FrozenArtifactError(f"{executable} ist kein lesbares PyInstaller-Artefakt") from exc
    members = set(archive.toc)
    for name in tuple(archive.toc):
        try:
            embedded = archive.open_embedded_archive(name)
        except Exception:
            continue
        members.update(embedded.toc)
    return members


def validate_members(label: str, members: set[str]) -> None:
    normalized = {name.replace("\\", "/").replace("/", ".").casefold() for name in members}
    for required in ("papagui_client", "papagui_contracts"):
        if not any(name == required or name.startswith(f"{required}.") for name in normalized):
            raise FrozenArtifactError(f"{label} enthält {required} nicht")
    forbidden_roots = ("papagui_server", "app")
    forbidden_modules = (
        "customer_recognition",
        "index_job",
        "index_manager",
        "index_writer",
    )
    violations = sorted(
        name
        for name in normalized
        if any(name == root or name.startswith(f"{root}.") for root in forbidden_roots)
        or any(name == module or name.endswith(f".{module}") for module in forbidden_modules)
    )
    if violations:
        raise FrozenArtifactError(
            f"{label} enthält verbotene Server-/Legacy-Module: {', '.join(violations)}"
        )


def run_checked(executable: Path, *arguments: str, environment: dict[str, str]) -> None:
    try:
        result = subprocess.run(
            [str(executable), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FrozenArtifactError(f"{executable.name} konnte nicht gestartet werden") from exc
    if result.returncode:
        details = (result.stderr or result.stdout).strip()
        raise FrozenArtifactError(
            f"{executable.name} {' '.join(arguments)} endete mit {result.returncode}: {details}"
        )


def validate(dist: Path, platform: str = sys.platform) -> None:
    products = executable_paths(dist, platform)
    for label, executable in products.items():
        if not executable.is_file():
            raise FrozenArtifactError(f"Native Datei fehlt: {executable}")
        validate_members(label, archive_members(executable))

    with TemporaryDirectory(prefix="papagui-frozen-smoke-") as directory:
        environment = os.environ.copy()
        environment.update(
            {
                "PAPAGUI_CLIENT_DATA_ROOT": str(Path(directory) / "data"),
                "PAPAGUI_CLIENT_CONFIG_PATH": str(Path(directory) / "client-config.json"),
                "QT_QPA_PLATFORM": "offscreen",
            }
        )
        run_checked(products["client"], "--help", environment=environment)
        run_checked(
            products["client"],
            "--sync-only",
            "--offline",
            environment=environment,
        )
        run_checked(products["tray"], "--help", environment=environment)
        for executable in products.values():
            run_checked(executable, "--update-probe", environment=environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    arguments = parser.parse_args(argv)
    try:
        validate(arguments.dist.resolve())
    except FrozenArtifactError as exc:
        print(f"Frozen-Artefaktprüfung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print("Frozen client/tray artifacts passed boundary and launch checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
