from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools.server_update import Configuration, ServerUpdater
from papagui_client.updates.feed import UpdateError
from papagui_client.updates.storage import atomic_json


def updater_fixture(tmp_path, monkeypatch, *, broken=False):
    data, config, state = (tmp_path / name for name in ("data", "config", "updates"))
    for path in (data, config):
        path.mkdir()
    (data / "customer.txt").write_text("original customer")
    (config / "api-token").write_text("synthetic token")
    configuration = Configuration((tmp_path / "compose.yaml",), tmp_path, "synthetic", "papagui-server", data, config, state)
    release = SimpleNamespace(version="0.4.5", server_image="ghcr.io/niclasgamepoint/ordnergui-server@sha256:" + "a" * 64)
    feed = SimpleNamespace(latest=lambda: release)
    updater = ServerUpdater(configuration, feed=feed, run=lambda *a, **k: "")
    monkeypatch.setattr("tools.server_update.private_directory", lambda p: p.mkdir(exist_ok=True))
    events = []
    running = {"value": True}
    def container(**_):
        return {"Image": "sha256:" + "b" * 64, "Config": {"Labels": {"org.opencontainers.image.version": "0.4.3"}}, "State": {"Running": running["value"]}}
    def stop():
        events.append("stop")
        running["value"] = False
    def start(image):
        events.append(image)
        running["value"] = True
        if image == release.server_image:
            assert (config / ".update-hold").exists()
            assert list((state / "backups").glob("*/data/customer.txt"))
            (data / "customer.txt").write_text("new schema")
    def health(expected):
        events.append("health:" + expected)
        if broken and expected == release.version:
            raise UpdateError("synthetic startup failure")
    monkeypatch.setattr(updater, "container", container)
    monkeypatch.setattr(updater, "stop", stop)
    monkeypatch.setattr(updater, "start", start)
    monkeypatch.setattr(updater, "health", health)
    return updater, events


def test_success_keeps_backup_and_releases_traffic_only_after_health(tmp_path, monkeypatch):
    updater, events = updater_fixture(tmp_path, monkeypatch)
    assert updater.tick()
    assert events[0] == "stop"
    assert events[-1] == "health:0.4.5"
    assert not updater.journal.exists()
    assert not (updater.config.config / ".update-hold").exists()
    saved = next((updater.config.state / "backups").glob("*/data/customer.txt"))
    assert saved.read_text() == "original customer"


def test_server_older_than_tested_baseline_is_not_stopped(tmp_path, monkeypatch):
    updater, events = updater_fixture(tmp_path, monkeypatch)
    current = updater.container()
    current["Config"]["Labels"]["org.opencontainers.image.version"] = "0.4.2"
    monkeypatch.setattr(updater, "container", lambda: current)
    with pytest.raises(UpdateError, match="manual server upgrade"):
        updater.tick()
    assert not events
    assert not updater.journal.exists()


def test_failed_candidate_restores_both_volumes_and_old_image(tmp_path, monkeypatch):
    updater, events = updater_fixture(tmp_path, monkeypatch, broken=True)
    with pytest.raises(UpdateError):
        updater.tick()
    assert (updater.config.data / "customer.txt").read_text() == "original customer"
    assert (updater.config.config / "api-token").read_text() == "synthetic token"
    assert any(path.name.startswith("data.failed-") for path in tmp_path.iterdir())
    assert events[-1] == "health:0.4.3"
    assert not updater.journal.exists()
    assert not updater.tick()  # Do not loop on a known broken release.


def test_power_loss_after_commit_does_not_restore_old_user_data(tmp_path, monkeypatch):
    updater, events = updater_fixture(tmp_path, monkeypatch)
    updater.config.state.mkdir()
    atomic_json(updater.journal, {"phase": "committed"})
    (updater.config.config / ".update-hold").touch()
    updater.recover()
    assert not events
    assert not updater.journal.exists()
    assert not (updater.config.config / ".update-hold").exists()


def test_backup_failure_never_starts_candidate(tmp_path, monkeypatch):
    updater, events = updater_fixture(tmp_path, monkeypatch)
    def no_space(*_):
        raise OSError("disk full")
    monkeypatch.setattr("tools.server_update.snapshot", no_space)
    with pytest.raises(OSError):
        updater.tick()
    assert (updater.config.data / "customer.txt").read_text() == "original customer"
    assert not any("ghcr.io" in event for event in events)
    assert not updater.journal.exists()


def test_configuration_rejects_nested_data_and_relative_paths(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.touch()
    raw = {"compose_files": [str(compose)], "project_directory": str(tmp_path), "project_name": "test", "data": str(tmp_path / "data"), "config": str(tmp_path / "config"), "state": str(tmp_path / "updates")}
    path = tmp_path / "updater.json"
    path.write_text(json.dumps(raw))
    assert Configuration.load(path).service == "papagui-server"
    raw["state"] = str(tmp_path / "data/updates")
    path.write_text(json.dumps(raw))
    with pytest.raises(UpdateError):
        Configuration.load(path)
    raw["state"] = "relative"
    path.write_text(json.dumps(raw))
    with pytest.raises(UpdateError):
        Configuration.load(path)
