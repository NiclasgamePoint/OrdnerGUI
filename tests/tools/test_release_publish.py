from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from papagui_client.updates.feed import API, MANIFEST_NAME, UpdateError


def publication(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    module = importlib.import_module("tools.publish_update_manifest")
    monkeypatch.setattr(module, "package_version", lambda _: "0.4.4")
    monkeypatch.setattr(module, "source_assets", lambda _: [])  # Validated separately.
    monkeypatch.setattr(module, "server_source_assets", lambda _: [])
    for platform in ("windows-x64", "linux-x64", "macos-x64", "macos-arm64"):
        content = platform.encode()
        name = "papagui-" + platform + ".tar.gz"
        (tmp_path / name).write_bytes(content)
        (tmp_path / (platform + ".json")).write_text(json.dumps({"platform": platform, "version": "0.4.4", "name": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}))
    release = {"id": 1, "tag_name": "0.4.4", "draft": False, "prerelease": False, "published_at": "2026-09-14T00:00:00Z", "assets": []}
    uploads = []
    def gh(*args):
        if args[0] == "api":
            return json.dumps(release["assets"] if "/assets?" in args[1] else release)
        assert args[:2] == ("release", "upload")
        path = Path(args[3])
        uploads.append(path.name)
        release["assets"].append({"name": path.name, "size": path.stat().st_size, "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), "state": "uploaded", "url": API + f"/releases/assets/{len(uploads)}"})
        return ""
    monkeypatch.setattr(module, "gh", gh)
    return module, release, uploads


def test_manifest_is_last_and_identical_rerun_is_safe(tmp_path, monkeypatch):
    module, _, uploads = publication(tmp_path, monkeypatch)
    module.publish(tmp_path, "0.4.4", "sha256:" + "a" * 64)
    assert len(uploads) == 5 and uploads[-1] == MANIFEST_NAME
    module.publish(tmp_path, "0.4.4", "sha256:" + "a" * 64)
    assert len(uploads) == 5


def test_incomplete_platform_set_never_uploads(tmp_path, monkeypatch):
    module, _, uploads = publication(tmp_path, monkeypatch)
    (tmp_path / "macos-arm64.json").unlink()
    with pytest.raises(UpdateError):
        module.publish(tmp_path, "0.4.4", "sha256:" + "a" * 64)
    assert not uploads


def test_conflicting_asset_prevents_activation_manifest(tmp_path, monkeypatch):
    module, release, uploads = publication(tmp_path, monkeypatch)
    release["assets"].append({"name": "papagui-linux-x64.tar.gz", "digest": "sha256:" + "b" * 64})
    with pytest.raises(UpdateError):
        module.publish(tmp_path, "0.4.4", "sha256:" + "a" * 64)
    assert MANIFEST_NAME not in uploads
