#!/usr/bin/env python3
"""Reject cross-product code in built PapaGUI wheels and source archives."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import zipfile


class ArtifactError(RuntimeError):
    """A built distribution violates a package boundary."""


@dataclass(frozen=True, slots=True)
class Artifact:
    path: Path
    members: tuple[str, ...]
    metadata: tuple[str, ...]

    @property
    def normalized_members(self) -> tuple[str, ...]:
        return tuple(name.replace("\\", "/") for name in self.members)


def _load(path: Path) -> Artifact:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = tuple(archive.namelist())
            metadata = tuple(
                archive.read(name).decode("utf-8", errors="replace")
                for name in names
                if name.endswith(".dist-info/METADATA")
            )
            return Artifact(path, names, metadata)
    if path.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(path) as archive:
            members = tuple(archive.getmembers())
            metadata: list[str] = []
            for member in members:
                if not member.isfile() or not member.name.endswith("PKG-INFO"):
                    continue
                stream = archive.extractfile(member)
                if stream is not None:
                    metadata.append(stream.read().decode("utf-8", errors="replace"))
            return Artifact(path, tuple(member.name for member in members), tuple(metadata))
    raise ArtifactError(f"Nicht unterstütztes Artefakt: {path}")


def _distribution(artifact: Artifact) -> str:
    filename = artifact.path.name.replace("-", "_").casefold()
    for name in ("papagui_contracts", "papagui_server", "papagui_client"):
        if filename.startswith(name):
            return name
    raise ArtifactError(f"Unbekannte PapaGUI-Distribution: {artifact.path.name}")


def _contains_package(artifact: Artifact, package: str) -> bool:
    for raw in artifact.normalized_members:
        parts = PurePosixPath(raw).parts
        if package in parts and raw.endswith((".py", "py.typed")):
            return True
    return False


def _canonical_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _requirement_name(value: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", value)
    if match is None:
        raise ArtifactError(f"Ungültiger Requires-Dist-Eintrag: {value!r}")
    return _canonical_distribution_name(match.group(1))


def _dependencies(artifact: Artifact, distribution: str) -> set[str]:
    if not artifact.metadata:
        raise ArtifactError(
            f"{artifact.path.name} muss mindestens einen METADATA-/PKG-INFO-Datensatz "
            "enthalten."
        )
    expected_name = distribution.replace("_", "-")
    dependency_sets: list[set[str]] = []
    for raw_metadata in artifact.metadata:
        metadata = Parser().parsestr(raw_metadata)
        declared_name = _canonical_distribution_name(metadata.get("Name", ""))
        if declared_name != expected_name:
            raise ArtifactError(
                f"{artifact.path.name} deklariert unerwarteten Paketnamen "
                f"{metadata.get('Name', '')!r}."
            )
        dependency_sets.append(
            {
                _requirement_name(requirement)
                for requirement in metadata.get_all("Requires-Dist", [])
            }
        )
    dependencies = dependency_sets[0]
    if any(candidate != dependencies for candidate in dependency_sets[1:]):
        raise ArtifactError(
            f"{artifact.path.name} enthält widersprüchliche Paketmetadaten."
        )
    return dependencies


def validate(artifact: Artifact) -> None:
    distribution = _distribution(artifact)
    if not _contains_package(artifact, distribution):
        raise ArtifactError(
            f"{artifact.path.name} enthält sein Paket {distribution} nicht."
        )
    forbidden = {
        "papagui_contracts": {"papagui_client", "papagui_server", "app"},
        "papagui_server": {"papagui_client", "app", "PySide6"},
        "papagui_client": {"papagui_server", "app"},
    }[distribution]
    violations = sorted(
        package for package in forbidden if _contains_package(artifact, package)
    )
    if violations:
        raise ArtifactError(
            f"{artifact.path.name} enthält verbotene Pakete: {', '.join(violations)}"
        )

    dependencies = _dependencies(artifact, distribution)
    if distribution == "papagui_contracts" and dependencies:
        raise ArtifactError(
            f"{artifact.path.name} darf keine Runtime-Abhängigkeiten deklarieren: "
            + ", ".join(sorted(dependencies))
        )
    forbidden_dependencies = {
        "papagui_server": {
            "app",
            "papagui-client",
        },
        "papagui_client": {"app", "papagui-server"},
    }.get(distribution, set())
    dependency_violations = sorted(dependencies & forbidden_dependencies)
    if distribution == "papagui_server":
        dependency_violations.extend(
            sorted(
                dependency
                for dependency in dependencies
                if dependency.startswith(("pyqt", "pyside"))
                and dependency not in dependency_violations
            )
        )
    if dependency_violations:
        raise ArtifactError(
            f"{artifact.path.name} deklariert verbotene Abhängigkeiten: "
            + ", ".join(dependency_violations)
        )
    if distribution in {"papagui_server", "papagui_client"}:
        if "papagui-contracts" not in dependencies:
            raise ArtifactError(
                f"{artifact.path.name} deklariert papagui-contracts nicht."
            )

    if distribution == "papagui_client":
        writer_names = {
            "index_job.py",
            "index_manager.py",
            "index_writer.py",
            "customer_recognition.py",
        }
        leaked = sorted(
            name
            for name in artifact.normalized_members
            if PurePosixPath(name).name.casefold() in writer_names
        )
        if leaked:
            raise ArtifactError(
                f"{artifact.path.name} enthält Index-Writer: {', '.join(leaked)}"
            )


def artifact_paths(inputs: list[Path]) -> list[Path]:
    result: list[Path] = []
    for candidate in inputs:
        if candidate.is_dir():
            result.extend(sorted(candidate.glob("*.whl")))
            result.extend(sorted(candidate.glob("*.tar.*")))
        elif candidate.is_file():
            result.append(candidate)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        artifacts = artifact_paths(arguments.paths)
        if not artifacts:
            raise ArtifactError("Keine Python-Artefakte gefunden.")
        for artifact_path in artifacts:
            validate(_load(artifact_path))
            print(f"OK: {artifact_path}")
        return 0
    except (ArtifactError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"Artefaktprüfung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
