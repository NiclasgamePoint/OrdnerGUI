from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from papagui_server.adapters.generations import GenerationV2Publisher


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE values_table(value TEXT)")
    connection.execute("INSERT INTO values_table VALUES (?)", (value,))
    connection.commit()
    connection.close()


def _publisher(tmp_path: Path) -> GenerationV2Publisher:
    data = tmp_path / "data"
    _database(data / "index" / "catalog" / "active.db", "index")
    _database(data / "customers.db", "customers")
    return GenerationV2Publisher(data)


def _artifacts(publisher: GenerationV2Publisher) -> dict[str, tuple[str, ...]]:
    return {
        component: tuple(
            path.name
            for path in sorted(
                (publisher.root / component).glob("*/*")
            )
            if path.is_file()
        )
        for component in ("index", "customers")
    }


def test_customer_stage_failure_never_activates_or_rotates_half_pair(
    tmp_path: Path, monkeypatch
) -> None:
    publisher = _publisher(tmp_path)
    before = publisher.publish_all()
    artifacts = _artifacts(publisher)
    original = publisher._stage

    def fail_customers(component, sources):
        if component == "customers":
            raise OSError("customer staging failed")
        return original(component, sources)

    monkeypatch.setattr(publisher, "_stage", fail_customers)
    with pytest.raises(OSError, match="customer staging failed"):
        publisher.publish_all()
    assert publisher.current() == before
    assert _artifacts(publisher) == artifacts


def test_global_pointer_failure_rolls_component_pointers_back(
    tmp_path: Path, monkeypatch
) -> None:
    publisher = _publisher(tmp_path)
    before = publisher.publish_all()
    artifacts = _artifacts(publisher)
    import papagui_server.adapters.generations as generations

    original = generations.atomic_json

    def fail_global(path, payload):
        if path == publisher.active_path:
            raise OSError("global switch failed")
        return original(path, payload)

    monkeypatch.setattr(generations, "atomic_json", fail_global)
    with pytest.raises(OSError, match="global switch failed"):
        publisher.publish_all()
    assert publisher.current() == before
    assert _artifacts(publisher) == artifacts
    for component in ("index", "customers"):
        pointer = publisher.root / component / "active-generation.json"
        assert json.loads(pointer.read_text(encoding="utf-8")) == before["components"][component]


def test_cleanup_failure_does_not_turn_a_committed_pair_into_failed_run(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    publisher = _publisher(tmp_path)
    first = publisher.publish_all()
    monkeypatch.setattr(
        publisher, "_prune", lambda _component: (_ for _ in ()).throw(OSError("busy"))
    )
    second = publisher.publish_all()
    assert publisher.current() == second
    assert second != first
    for component, descriptor in second["components"].items():
        assert publisher.archive_path(component, descriptor["generation"]).is_file()
    assert "Generationsbereinigung für index fehlgeschlagen" in caplog.text


def test_cleanup_failure_is_durable_and_next_publication_repairs_retention(
    tmp_path: Path, monkeypatch
) -> None:
    publisher = _publisher(tmp_path)
    publisher.publish_all()
    monkeypatch.setattr(
        publisher,
        "_prune",
        lambda _component: (_ for _ in ()).throw(PermissionError("read-only")),
    )
    for _ in range(4):
        publisher.publish_all()

    status = publisher.retention_status()
    assert status["state"] == "degraded"
    assert status["required_predecessors"] == 3
    assert set(status["failures"]) == {"index", "customers"}
    assert "read-only" in status["failures"]["index"]["message"]
    assert publisher.retention_status_path.is_file()
    assert len(list((publisher.root / "index" / "archives").glob("*.zip"))) == 5

    # A process restart retains the diagnosis. The next safe publication first
    # repairs the old backlog and then enforces active + three predecessors.
    monkeypatch.undo()
    restarted = GenerationV2Publisher(publisher.data_path)
    assert restarted.retention_status()["state"] == "degraded"
    latest = restarted.publish_all()
    assert restarted.retention_status() == {
        "state": "ok",
        "required_predecessors": 3,
        "failures": {},
    }
    for component in ("index", "customers"):
        archives = list((restarted.root / component / "archives").glob("*.zip"))
        assert len(archives) == 4
        active = latest["components"][component]["generation"]
        assert (restarted.root / component / "archives" / f"{active}.zip").is_file()


def test_retention_count_is_not_runtime_configurable(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        GenerationV2Publisher(tmp_path / "data", backup_count=2)  # type: ignore[call-arg]


def test_pair_rotation_retains_active_plus_exactly_three_predecessors(
    tmp_path: Path,
) -> None:
    publisher = _publisher(tmp_path)
    latest = None
    for number in range(6):
        latest = publisher.publish_all()
        connection = sqlite3.connect(publisher.data_path / "customers.db")
        connection.execute("UPDATE values_table SET value=?", (str(number),))
        connection.commit()
        connection.close()
    assert publisher.current() == latest
    for component in ("index", "customers"):
        archives = list((publisher.root / component / "archives").glob("*.zip"))
        manifests = list((publisher.root / component / "manifests").glob("*.json"))
        assert len(archives) == 4
        assert len(manifests) == 4
        active = latest["components"][component]["generation"]
        assert (publisher.root / component / "archives" / f"{active}.zip").is_file()
