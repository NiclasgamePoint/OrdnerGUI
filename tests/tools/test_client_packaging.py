from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import runpy
import re
import sys
import struct
from types import SimpleNamespace
import tomllib

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging" / "client"
SPECS = {
    "papagui-client": PACKAGING / "pyinstaller" / "papagui-client.spec",
    "papagui-tray": PACKAGING / "pyinstaller" / "papagui-tray.spec",
}


@dataclass(frozen=True)
class BuildCall:
    kind: str
    arguments: tuple[object, ...]
    options: dict[str, object]


def _evaluate_spec(path: Path, platform: str, monkeypatch: pytest.MonkeyPatch):
    calls: list[BuildCall] = []

    def analysis(*arguments, **options):
        calls.append(BuildCall("Analysis", arguments, options))
        return SimpleNamespace(pure=(), scripts=(), binaries=(), datas=())

    def target(kind: str):
        def build(*arguments, **options):
            call = BuildCall(kind, arguments, options)
            calls.append(call)
            return call

        return build

    monkeypatch.setattr(sys, "platform", platform)
    namespace = runpy.run_path(
        str(path),
        init_globals={
            "SPEC": str(path),
            "Analysis": analysis,
            "PYZ": target("PYZ"),
            "EXE": target("EXE"),
            "BUNDLE": target("BUNDLE"),
        },
    )
    return namespace, calls


def test_build_matrix_and_workflow_cover_exactly_four_release_targets() -> None:
    matrix = json.loads((PACKAGING / "build-matrix.json").read_text(encoding="utf-8"))
    targets = {(item["os"], item["arch"], item["runner"]) for item in matrix["targets"]}
    assert targets == {
        ("linux", "x86_64", "ubuntu-24.04"),
        ("windows", "x86_64", "windows-latest"),
        ("macos", "x86_64", "macos-15-intel"),
        ("macos", "arm64", "macos-14"),
    }
    assert matrix["entrypoints"] == ["papagui-client", "papagui-tray"]
    assert matrix["publish"] is False
    assert matrix["signed"] is False

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "client-artifacts.yml").read_text(encoding="utf-8")
    )
    workflow_targets = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert {(item["os"], item["artifact"]) for item in workflow_targets} == {
        ("ubuntu-24.04", "linux-x64"),
        ("windows-latest", "windows-x64"),
        ("macos-15-intel", "macos-x64"),
        ("macos-14", "macos-arm64"),
    }


@pytest.mark.parametrize(
    ("target_os", "architecture", "platform", "expects_bundle"),
    [
        ("linux", "x86_64", "linux", False),
        ("windows", "x86_64", "win32", False),
        ("macos", "x86_64", "darwin", True),
        ("macos", "arm64", "darwin", True),
    ],
)
@pytest.mark.parametrize("entrypoint", ("papagui-client", "papagui-tray"))
def test_specs_select_native_shape_for_every_release_target(
    target_os: str,
    architecture: str,
    platform: str,
    expects_bundle: bool,
    entrypoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del target_os, architecture  # The native CI runner selects the actual architecture.
    namespace, calls = _evaluate_spec(SPECS[entrypoint], platform, monkeypatch)
    executable = next(call for call in calls if call.kind == "EXE")
    analysis = next(call for call in calls if call.kind == "Analysis")
    bundles = [call for call in calls if call.kind == "BUNDLE"]

    assert executable.options["name"] == entrypoint
    assert executable.options["console"] is False
    icon_role = "client" if entrypoint == "papagui-client" else "server"
    icons = ROOT / "packages/client/src/papagui_client/resources/icons"
    assert (str(icons), "papagui_client/resources/icons") in analysis.options["datas"]
    if platform == "win32":
        assert executable.options["icon"] == str(icons / f"papagui-{icon_role}.ico")
    else:
        assert executable.options["icon"] is None
    assert set(analysis.options["excludes"]) == {
        "papagui_server",
        "app",
        "index_job",
        "index_manager",
        "index_writer",
        "customer_recognition",
    }
    assert len(bundles) == int(expects_bundle)
    if expects_bundle:
        bundle = bundles[0]
        expected_name = (
            "PapaGUI Client.app" if entrypoint == "papagui-client" else "PapaGUI Tray.app"
        )
        assert bundle.options["name"] == expected_name
        assert bundle.options["icon"] == str(icons / f"papagui-{icon_role}.icns")
        assert bundle.options["bundle_identifier"].startswith("de.papagui.")
        assert bundle.options["info_plist"]["CFBundleShortVersionString"] == "0.4.4"
        assert bundle.options["info_plist"]["CFBundleVersion"] == "0.4.4"
        assert namespace["application"] == bundle
    else:
        assert "application" not in namespace


@pytest.mark.parametrize("role", ("client", "server"))
def test_native_icon_assets_contain_small_and_high_dpi_sizes(role: str) -> None:
    icons = ROOT / "packages/client/src/papagui_client/resources/icons"
    ico = (icons / f"papagui-{role}.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", ico)
    assert (reserved, kind) == (0, 1)
    sizes = set()
    for index in range(count):
        width, height, _, _, _, depth, length, offset = struct.unpack_from(
            "<BBBBHHII", ico, 6 + index * 16
        )
        assert (width or 256) == (height or 256)
        assert depth == 32
        assert length > 0 and offset + length <= len(ico)
        sizes.add(width or 256)
    assert {16, 20, 24, 32, 40, 48, 64, 96, 128, 256} <= sizes

    icns = (icons / f"papagui-{role}.icns").read_bytes()
    magic, length = struct.unpack_from(">4sI", icns)
    assert magic == b"icns" and length == len(icns)
    offset = 8
    types = set()
    while offset < len(icns):
        kind, length = struct.unpack_from(">4sI", icns, offset)
        assert length > 8 and offset + length <= len(icns)
        types.add(kind)
        offset += length
    assert {b"ic07", b"ic08", b"ic09", b"ic10"} <= types  # 128 through 1024 pixels.


def test_ci_uses_component_and_build_tool_locks() -> None:
    quality = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    artifacts = (ROOT / ".github" / "workflows" / "client-artifacts.yml").read_text(
        encoding="utf-8"
    )

    assert "packages/server/requirements-lock.txt" in quality
    assert "packages/client/requirements-lock.txt" in quality
    assert "packaging/requirements-build-lock.txt" in quality
    assert "packages/client/requirements-lock.txt" in artifacts
    assert "packaging/client/requirements-build-lock.txt" in artifacts
    assert "packages/client[gui]" not in artifacts
    assert "pip install pyinstaller" not in artifacts
    assert "python -m build --no-isolation packages/contracts" in quality
    assert "python -m build --no-isolation packages/server" in quality
    assert "python -m build --no-isolation packages/client" in quality
    assert "repository-regression" in quality
    assert "tools/check_frozen_client.py dist" in artifacts
    assert "PapaGUI Client.app/Contents/MacOS/papagui-tray" not in artifacts
    assert "PapaGUI Tray.app" not in artifacts


def test_build_tool_locks_pin_complete_direct_dependency_closures() -> None:
    python_build = _locked_names(ROOT / "packaging" / "requirements-build-lock.txt")
    assert {
        "build",
        "colorama",
        "packaging",
        "pyproject-hooks",
        "setuptools",
        "wheel",
    } <= python_build

    native_build = _locked_names(PACKAGING / "requirements-build-lock.txt")
    assert {
        "altgraph",
        "macholib",
        "packaging",
        "pefile",
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "pywin32-ctypes",
        "setuptools",
    } <= native_build


def _requirement_name(value: str) -> str:
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", value)
    assert match is not None, value
    return re.sub(r"[-_.]+", "-", match.group(1)).casefold()


def _locked_names(path: Path) -> set[str]:
    return {
        _requirement_name(line)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith(("#", "-"))
    }


@pytest.mark.parametrize("component", ("server", "client"))
def test_component_runtime_locks_cover_declared_direct_dependencies(
    component: str,
) -> None:
    root = ROOT / "packages" / component
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = list(metadata["project"].get("dependencies", ()))
    if component == "client":
        declared.extend(metadata["project"]["optional-dependencies"]["gui"])
    expected = {
        _requirement_name(requirement)
        for requirement in declared
        if _requirement_name(requirement) != "papagui-contracts"
    }
    assert expected <= _locked_names(root / "requirements-lock.txt")


def test_workflow_and_compose_yaml_files_are_parseable() -> None:
    paths = [
        *sorted((ROOT / ".github" / "workflows").glob("*.yml")),
        ROOT / "compose.yaml",
        ROOT / "deploy" / "server" / "compose.yaml",
    ]
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path
