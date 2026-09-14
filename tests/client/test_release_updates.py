from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from pathlib import Path
import tarfile
from types import SimpleNamespace
import urllib.request

import pytest

from papagui_client.updates import feed, runtime, storage


def metadata():
    digest = "a" * 64
    assets = []
    clients = {}
    for number, key in enumerate(("windows-x64", "linux-x64", "macos-x64", "macos-arm64"), 1):
        name = f"papagui-{key}.tar.gz"
        clients[key] = {"name": name, "size": 5, "sha256": digest}
        assets.append({"name": name, "size": 5, "digest": "sha256:" + digest, "state": "uploaded", "url": feed.API + f"/releases/assets/{number}"})
    release = {"tag_name": "0.4.5", "published_at": "2026-09-15T00:00:00Z", "draft": False, "prerelease": False, "assets": assets}
    manifest = {"schema": 1, "version": "0.4.5", "data_epoch": 1, "api_versions": ["2", "1"], "compatibility_floor": "0.4.3", "clients": clients, "server_image": feed.IMAGE_REPOSITORY + "@sha256:" + digest}
    return release, manifest


@pytest.mark.parametrize("value", ("v0.4.5", "0.4.5-rc1", "0.4", "01.2.3", "../../file", None, 4))
def test_only_stable_versions_are_accepted(value):
    with pytest.raises(feed.UpdateError):
        feed.version(value)


def test_complete_release_contract():
    release, manifest = metadata()
    parsed = feed.Release.parse(manifest, release)
    assert parsed.version == "0.4.5"
    assert set(parsed.clients) == set(manifest["clients"])
    assert feed.version("0.10.0") > feed.version("0.9.9")


@pytest.mark.parametrize("field,value", (("schema", 2), ("data_epoch", 2), ("api_versions", ["3"]), ("compatibility_floor", "0.4.4"), ("version", "0.4.6"), ("server_image", "attacker/image:latest"), ("clients", {})))
def test_incompatible_or_incomplete_release_is_rejected(field, value):
    release, manifest = metadata()
    manifest[field] = value
    with pytest.raises(feed.UpdateError):
        feed.Release.parse(manifest, release)


@pytest.mark.parametrize("field,value", (("prerelease", True), ("draft", True), ("published_at", None)))
def test_test_releases_never_roll_out(field, value):
    release, manifest = metadata()
    release[field] = value
    with pytest.raises(feed.UpdateError):
        feed.Release.parse(manifest, release)


@pytest.mark.parametrize("field,value", (("size", 9), ("digest", "sha256:" + "b" * 64), ("state", "new"), ("url", "https://api.github.com/repos/other/repo/releases/assets/1")))
def test_asset_must_match_github_metadata(field, value):
    release, manifest = metadata()
    release["assets"][0][field] = value
    with pytest.raises(feed.UpdateError):
        feed.Release.parse(manifest, release)


@pytest.mark.parametrize("url", ("http://github.com/a", "https://attacker.test/a", "https://token@github.com/a", "file:///tmp/a", "https://github.com:444/a"))
def test_untrusted_download_hosts_are_rejected(url):
    with pytest.raises(feed.UpdateError):
        feed.checked_url(url)


def test_redirect_strips_private_repository_credentials():
    request = urllib.request.Request(feed.API, headers={"Authorization": "Bearer secret"})
    redirected = feed.ReleaseRedirect().redirect_request(request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/example")
    assert redirected.get_header("Authorization") is None


def test_download_verifies_size_hash_and_preserves_existing_file(tmp_path):
    content = b"verified application"
    opener = SimpleNamespace(open=lambda *a, **k: io.BytesIO(content))
    client = feed.ReleaseFeed(opener=opener)
    asset = feed.Asset("app.tar.gz", len(content), hashlib.sha256(content).hexdigest(), feed.API + "/releases/assets/1")
    target = tmp_path / "app.tar.gz"
    client.download(asset, target)
    assert target.read_bytes() == content
    with pytest.raises(FileExistsError):
        client.download(asset, target)
    assert target.read_bytes() == content
    with pytest.raises(feed.UpdateError):
        client.download(feed.Asset(asset.name, asset.size, "a" * 64, asset.url), tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_feed_waits_for_last_uploaded_manifest(tmp_path):
    release, manifest = metadata()
    data = json.dumps(manifest).encode()
    release["assets"].append({"name": feed.MANIFEST_NAME, "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "url": feed.API + "/releases/assets/10", "state": "uploaded"})
    responses = [json.dumps(release).encode(), data]
    client = feed.ReleaseFeed(opener=SimpleNamespace(open=lambda *a, **k: io.BytesIO(responses.pop(0))))
    assert client.latest().version == "0.4.5"
    release["assets"].pop()
    responses.append(json.dumps(release).encode())
    assert client.latest() is None


@pytest.mark.parametrize("name,link", (("../escape", None), ("/absolute", None), ("a\\b", None), ("safe", "../../escape")))
def test_archive_cannot_escape_staging(tmp_path, name, link):
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo(name)
        if link:
            member.type, member.linkname = tarfile.SYMTYPE, link
        tar.addfile(member)
    with pytest.raises(feed.UpdateError):
        storage.extract_bundle(archive, tmp_path / "stage")
    assert not (tmp_path / "escape").exists()


def test_process_lease_blocks_activation_and_stale_files_recover(tmp_path):
    root = tmp_path
    path = root / "sessions/a.lock"
    with storage.FileLock(path):
        assert not storage.idle(root)
    assert storage.idle(root)
    assert not path.exists()


def test_candidate_is_probed_and_backed_up_before_switch(tmp_path, monkeypatch):
    root, data = tmp_path / "updates", tmp_path / "data"
    root.mkdir()
    data.mkdir()
    (data / "notes.txt").write_text("never lose this")
    storage.atomic_json(root / "ready.json", {"version": "0.4.5"})
    calls = []
    def probe(command, **kwargs):
        calls.append(command)
        assert command[-1] == "--update-probe"
        assert not (root / "active.json").exists()
        assert Path(kwargs["env"]["PAPAGUI_CLIENT_DATA_ROOT"]) != data
        return SimpleNamespace(returncode=0)
    active = runtime.activate(root, data, probe=probe)
    assert active["version"] == "0.4.5"
    assert len(calls) == 2
    assert (root / "backups" / active["backup"] / "data/notes.txt").read_text() == "never lose this"
    assert (data / "notes.txt").read_text() == "never lose this"


def test_failed_probe_and_busy_profile_leave_installed_version_untouched(tmp_path):
    root = tmp_path / "updates"
    root.mkdir()
    storage.atomic_json(root / "ready.json", {"version": "0.4.5"})
    with storage.FileLock(root / "sessions/client.lock"):
        assert runtime.activate(root, tmp_path / "data") == {}
    with pytest.raises(feed.UpdateError):
        runtime.activate(root, tmp_path / "data", probe=lambda *a, **k: SimpleNamespace(returncode=1))
    assert not (root / "active.json").exists()
    assert runtime.read_state(root / "rejected.json")["version"] == "0.4.5"
    def unexpected_probe(*a, **k):
        pytest.fail("A rejected release must not be probed repeatedly")
    assert runtime.activate(root, tmp_path / "data", probe=unexpected_probe) == {}


def test_backup_failure_leaves_pointer_unchanged(tmp_path, monkeypatch):
    root = tmp_path / "updates"
    root.mkdir()
    storage.atomic_json(root / "ready.json", {"version": "0.4.5"})
    monkeypatch.setattr(runtime, "snapshot", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        runtime.activate(root, tmp_path / "data", probe=lambda *a, **k: SimpleNamespace(returncode=0))
    assert not (root / "active.json").exists()


def test_source_launch_does_not_download_or_replace_itself(monkeypatch):
    monkeypatch.delattr(runtime.sys, "frozen", raising=False)
    assert runtime.launch("client", []) is None
    monkeypatch.setenv("PAPAGUI_UPDATER_CHILD", "1")
    assert runtime.launch("tray", []) is None
    assert "PAPAGUI_UPDATER_CHILD" not in os.environ


def test_private_directory_removes_explicit_access_for_other_users(tmp_path):
    target = tmp_path / "private"
    target.mkdir()
    if os.name == "nt":
        subprocess.run(["icacls", str(target), "/grant", "*S-1-1-0:(OI)(CI)R"], check=True, capture_output=True)
    storage.private_directory(target)
    (target / "writable").write_text("synthetic")
    if os.name == "nt":
        env = os.environ.copy()
        env.pop("PSModulePath", None)
        env["PAPAGUI_PRIVATE_DIRECTORY"] = str(target)
        command = "$ErrorActionPreference='Stop'; $acl=Get-Acl -LiteralPath $env:PAPAGUI_PRIVATE_DIRECTORY; $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; if (-not $acl.AreAccessRulesProtected) { exit 2 }; foreach ($r in $acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) { if ($r.IdentityReference -ne $sid -or $r.AccessControlType -ne 'Allow') { exit 3 } }"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], env=env, check=True, capture_output=True)
    else:
        assert target.stat().st_mode & 0o777 == 0o700


def test_connection_failure_removes_partial_download(tmp_path):
    def unavailable(*_, **__):
        raise OSError("offline")
    client = feed.ReleaseFeed(opener=SimpleNamespace(open=unavailable))
    with pytest.raises(OSError):
        client.download(feed.Asset("a", 1, "a" * 64, feed.API + "/releases/assets/1"), tmp_path / "download")
    assert not (tmp_path / "download").exists()


def test_stage_recovers_crash_after_verified_directory_rename(tmp_path):
    release = feed.Release.parse(metadata()[1], metadata()[0])
    target = tmp_path / "versions" / release.version
    for role in ("client", "tray"):
        program = runtime.executable(target, role)
        program.parent.mkdir(parents=True, exist_ok=True)
        program.touch()
    assert runtime.stage(tmp_path, "0.4.4", SimpleNamespace(latest=lambda: release))
    assert runtime.read_state(tmp_path / "ready.json")["version"] == "0.4.5"


def test_newer_manual_installation_prevents_staged_downgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "__version__", "0.4.6")
    storage.atomic_json(tmp_path / "active.json", {"version": "0.4.4"})
    storage.atomic_json(tmp_path / "ready.json", {"version": "0.4.5"})
    assert runtime.activate(tmp_path, tmp_path / "data")["version"] == "0.4.4"
    assert not (tmp_path / "backups").exists()


def test_failed_installed_startup_selects_previous_program_without_restoring_user_data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "notes.txt").write_text("user edit")
    root = tmp_path / "data-updates"
    root.mkdir()
    program = runtime.executable(root / "versions/0.4.5", "client")
    program.parent.mkdir(parents=True)
    program.touch()
    storage.atomic_json(root / "active.json", {"version": "0.4.5", "previous": "0.4.4"})
    monkeypatch.setattr(runtime.sys, "frozen", True, raising=False)
    monkeypatch.setenv("PAPAGUI_CLIENT_DATA_ROOT", str(data))
    monkeypatch.setenv("PAPAGUI_AUTO_UPDATE", "0")
    monkeypatch.setattr(runtime, "private_directory", lambda _: None)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    assert runtime.launch("client", []) == 1
    assert runtime.read_state(root / "active.json")["version"] == "0.4.4"
    assert runtime.read_state(root / "rejected.json")["version"] == "0.4.5"
    assert storage.idle(root)
    assert (data / "notes.txt").read_text() == "user edit"


def test_one_shot_sync_holds_profile_lease_without_background_download(tmp_path, monkeypatch):
    root = tmp_path / "data-updates"
    root.mkdir()
    monkeypatch.setattr(runtime.sys, "frozen", True, raising=False)
    monkeypatch.setenv("PAPAGUI_CLIENT_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(runtime, "private_directory", lambda _: None)
    monkeypatch.setattr(runtime.threading.Thread, "start", lambda _: pytest.fail("No updater thread during one-shot sync"))
    def sync(command, **_):
        assert "--sync-only" in command
        assert not storage.idle(root)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, "run", sync)
    assert runtime.launch("client", ["--sync-only"]) == 0
    assert storage.idle(root)
