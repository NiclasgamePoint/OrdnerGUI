from __future__ import annotations

import json
from pathlib import Path

import pytest

from papagui_server.adapters.generations import sha256_file
from papagui_server.adapters.source_guard import PersistentSourceIdentityGuard
from papagui_server.composition import RuntimeConfiguration, build_container
from papagui_server.domain.errors import SourceUnavailableError


def test_source_identity_requires_explicitly_allowed_first_initialization(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "Planung").mkdir(parents=True)
    state = tmp_path / "config" / "source-identity.json"
    strict = PersistentSourceIdentityGuard(state, allow_initialize=False)
    assert strict.probe(source, "primary") == (
        False,
        "Quellidentität ist noch nicht initialisiert.",
    )
    with pytest.raises(SourceUnavailableError):
        strict.ensure_ready(source, "primary")

    initializer = PersistentSourceIdentityGuard(state, allow_initialize=True)
    initializer.ensure_ready(source, "primary")
    assert json.loads(state.read_text(encoding="utf-8"))["sentinel_entries"] == [
        "planung"
    ]
    assert strict.probe(source, "primary") == (True, "")
    assert strict.probe(source, "other")[0] is False
    (source / "Planung").rename(source / "Replaced")
    assert strict.probe(source, "primary")[0] is False


def test_empty_or_unreadable_mount_is_never_considered_available(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    guard = PersistentSourceIdentityGuard(
        tmp_path / "source-identity.json", allow_initialize=True
    )
    assert guard.probe(source, "primary")[0] is False
    (source / ".DS_Store").write_text("metadata", encoding="utf-8")
    assert guard.probe(source, "primary")[0] is False
    assert guard.probe(tmp_path / "missing", "primary")[0] is False

    state = tmp_path / "broken.json"
    state.write_text("not-json", encoding="utf-8")
    recoverable = PersistentSourceIdentityGuard(state, allow_initialize=True)
    (source / "Planung").mkdir()
    assert recoverable.probe(source, "primary")[0] is False


def test_disappearing_empty_mount_preserves_active_catalog_and_generation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    document = source / "Planung" / "2026" / "Acme GmbH" / "file.txt"
    document.parent.mkdir(parents=True)
    document.write_text("index data", encoding="utf-8")
    container = build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=tmp_path / "data",
            config_path=tmp_path / "config",
            allow_insecure_no_client_token=True,
            initialize_source_identity=True,
        )
    )
    assert container.coordinator.run_once(full_rebuild=True) == 0
    before_generation = container.publisher.current()
    catalog = container.configuration.data_path / "index" / "catalog" / "active.db"
    before_catalog = sha256_file(catalog)

    source.rename(tmp_path / "detached-source")
    source.mkdir()
    status = container.coordinator.status()
    assert status["state"] == "degraded"
    assert status["source_available"] is False
    assert container.coordinator.run_once() == 1
    assert container.publisher.current() == before_generation
    assert sha256_file(catalog) == before_catalog
