from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest
import yaml

from papagui_client.updates.feed import MANIFEST_NAME, UpdateError


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    module = importlib.import_module("publish_beta_release")
    monkeypatch.setattr(module, "version", lambda _: "0.4.4")
    release = {"id": 1, "tag_name": "0.4.4", "prerelease": True,
               "draft": False, "published_at": "2026-09-14T00:00:00Z"}
    monkeypatch.setattr(module, "gh", lambda *_: json.dumps(release))
    uploads = []
    monkeypatch.setattr(module, "upload_asset", lambda _, path: uploads.append(path.name))
    monkeypatch.setattr(module, "source_assets", lambda _: [])  # Validated separately.
    monkeypatch.setattr(module, "server_source_assets", lambda _: [])
    for name in ("papagui-client-0.4.4-windows-x64-setup-unsigned.exe",
                 "papagui-client-0.4.4-linux-x64.deb"):
        (tmp_path / name).write_bytes(b"synthetic installer")
        digest = hashlib.sha256(b"synthetic installer").hexdigest()
        (tmp_path / (name + ".sha256")).write_text(f"{digest}  {name}\n", encoding="ascii")
    return module, release, uploads


def test_beta_publishes_two_installers_without_activation_or_macos(tmp_path, publication):
    module, _, uploads = publication
    for extra in (MANIFEST_NAME, "papagui-client-0.4.4-macos-x64-unsigned.pkg",
                  "papagui-client-0.4.4-windows-x64-update.tar.gz"):
        (tmp_path / extra).write_text("must not be published", encoding="utf-8")
    module.publish(tmp_path, "0.4.4", "sha256:" + "a" * 64)
    assert len(uploads) == 5
    assert uploads[-1] == "papagui-beta.json"
    assert MANIFEST_NAME not in uploads
    assert not any("macos" in name or "update.tar" in name for name in uploads)
    metadata = json.loads((tmp_path / "papagui-beta.json").read_text())
    assert metadata["automatic_updates"] is False
    assert metadata["server_image"].endswith("@sha256:" + "a" * 64)


@pytest.mark.parametrize("problem", ["missing", "checksum", "duplicate", "draft", "stable", "tag", "digest"])
def test_invalid_beta_never_uploads(tmp_path, publication, problem):
    module, release, uploads = publication
    artifact = tmp_path / "papagui-client-0.4.4-linux-x64.deb"
    if problem == "missing":
        artifact.unlink()
    elif problem == "checksum":
        artifact.write_bytes(b"changed after hashing")
    elif problem == "duplicate":
        (tmp_path / "duplicate").mkdir()
        (tmp_path / "duplicate" / artifact.name).write_bytes(artifact.read_bytes())
    elif problem == "draft":
        release["draft"] = True
    elif problem == "stable":
        release["prerelease"] = False
    with pytest.raises(UpdateError):
        module.publish(tmp_path, "0.4.3" if problem == "tag" else "0.4.4",
                       "latest" if problem == "digest" else "sha256:" + "a" * 64)
    assert uploads == []


def test_beta_and_stable_workflow_publication_are_separate():
    root = Path(__file__).resolve().parents[2]
    jobs = yaml.safe_load((root / ".github/workflows/release.yml").read_text())["jobs"]
    for name in ("quality", "clients"):
        assert jobs[name]["with"]["windows-linux-only"] == "${{ github.event.release.prerelease }}"
        assert "if" not in jobs[name]
    steps = jobs["publish"]["steps"]
    beta = next(step for step in steps if "publish_beta_release.py" in step.get("run", ""))
    stable = next(step for step in steps if "publish_update_manifest.py" in step.get("run", ""))
    assert beta["if"] == "github.event.release.prerelease == true"
    assert stable["if"] == "github.event.release.prerelease == false"
