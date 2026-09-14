"""Release guards: no environment inventory leaks and no missing legal payload."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def release_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    return importlib.import_module("release_metadata"), importlib.import_module("collect_licenses")


def test_package_licenses_and_versions_match_sources(release_tools):
    release, _ = release_tools
    release.check()
    assert release.version("client") == "0.4.4"
    assert release.version("server") == "0.4.4"
    assert release.version("contracts") == "0.4.4"


def test_license_inventory_uses_only_lock_and_rejects_version_drift(release_tools, monkeypatch, tmp_path):
    _, collector = release_tools
    root = tmp_path / "project"
    (root / "packages/server").mkdir(parents=True)
    (root / "packages/server/requirements-lock.txt").write_text("example==1.0\n", encoding="utf-8")
    for name in collector.LICENSE_FILES:
        (root / name).write_text("Example license", encoding="utf-8")
    base = tmp_path / "python"
    base.mkdir()
    (base / "LICENSE.txt").write_text("Python license", encoding="utf-8")
    monkeypatch.setattr(collector, "ROOT", root)
    monkeypatch.setattr(sys, "base_prefix", str(base))
    requested = []

    class Metadata(dict):
        def get_all(self, name, default):
            return default

    dist = SimpleNamespace(version="1.0", metadata=Metadata(Name="example", License="MIT"),
                           files=[], read_text=lambda _: "License: MIT\n")

    def distribution(name):
        requested.append(name)
        return dist

    monkeypatch.setattr(collector.metadata, "distribution", distribution)
    output = tmp_path / "notices"
    collector.collect("server", output)
    inventory = json.loads((output / "dependency-inventory.json").read_text(encoding="utf-8"))
    assert requested == ["example"]
    assert inventory["packages"][0]["version"] == "1.0"
    assert (output / "PYTHON-LICENSE.txt").is_file()
    with pytest.raises(FileExistsError):
        collector.collect("server", output)
    dist.version = "2.0"
    with pytest.raises(RuntimeError, match="differs from lock"):
        collector.collect("server", tmp_path / "wrong-version")


def test_windows_installer_is_per_user_and_has_no_data_deletion():
    script = (ROOT / "packaging/client/windows/papagui.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in script
    assert "papagui-client.exe" in script and "papagui-tray.exe" in script
    assert "[UninstallDelete]" not in script
    assert "[Run]" not in script


def test_linux_payload_keeps_notices_with_application(release_tools, monkeypatch, tmp_path):
    import os

    builder = importlib.import_module("build_installers")
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in ("papagui-client", "papagui-tray"):
        (dist / name).write_bytes(b"native executable fixture")

    def notices(component, output):
        output.mkdir(parents=True)
        (output / "LICENSE").write_text("license fixture", encoding="utf-8")
        (output / "LICENSE").chmod(0o777)  # Windows-mounted input permissions

    def package(*command):
        assert command[0] == "dpkg-deb"
        payload = Path(command[-2])
        license_file = payload / "opt/papagui/licenses/LICENSE"
        assert license_file.is_file()  # Survives /usr/share/doc exclusions.
        assert (payload / "usr/share/doc/papagui-client/copyright").is_file()
        if os.name != "nt":
            assert license_file.stat().st_mode & 0o777 == 0o644
            assert (payload / "opt/papagui/papagui-client").stat().st_mode & 0o777 == 0o755
        Path(command[-1]).write_bytes(b"deb fixture")

    monkeypatch.setattr(builder.sys, "platform", "linux")
    monkeypatch.setattr(builder.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(builder, "collect", notices)
    monkeypatch.setattr(builder, "run", package)
    artifact = builder.build(dist, tmp_path / "output")
    assert artifact.with_name(artifact.name + ".sha256").is_file()
