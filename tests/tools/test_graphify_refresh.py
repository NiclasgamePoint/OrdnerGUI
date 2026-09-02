from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools import graphify_refresh


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet")
    (tmp_path / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.py")
    return tmp_path


def test_fingerprint_is_stable_and_ignores_graph_output(repository: Path) -> None:
    first = graphify_refresh.source_fingerprint(repository)
    graph_dir = repository / graphify_refresh.OUTPUT_DIR
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}", encoding="utf-8")
    (repository / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")

    second = graphify_refresh.source_fingerprint(repository)

    assert first[0] != second[0]  # the tracked ignore policy itself is an input
    assert second[1] == 2
    (graph_dir / "graph.json").write_text('{"changed": true}', encoding="utf-8")
    assert graphify_refresh.source_fingerprint(repository) == second


def test_check_state_detects_source_drift(repository: Path) -> None:
    fingerprint, file_count = graphify_refresh.source_fingerprint(repository)
    (repository / graphify_refresh.STATE_FILE).write_text(
        json.dumps({"fingerprint": fingerprint, "file_count": file_count}),
        encoding="utf-8",
    )
    graphify_refresh.check_state(repository, require_local_graph=False)

    (repository / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(graphify_refresh.GraphifyToolError, match="veraltet"):
        graphify_refresh.check_state(repository, require_local_graph=False)


def test_graphify_executable_prefers_explicit_configuration(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = repository / "graphify"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setenv("GRAPHIFY_BIN", str(executable))

    assert graphify_refresh.graphify_executable(repository) == str(executable)


def test_graph_commit_returns_none_for_invalid_output(repository: Path) -> None:
    output = repository / graphify_refresh.OUTPUT_DIR
    output.mkdir()
    (output / "graph.json").write_text("{}", encoding="utf-8")

    assert graphify_refresh.graph_commit(repository) is None
    (output / "graph.json").write_text(
        json.dumps({"built_at_commit": "abc123"}), encoding="utf-8"
    )
    assert graphify_refresh.graph_commit(repository) == "abc123"


def test_local_graph_gate_requires_outputs_direction_packages_and_head(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repository / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")
    output = repository / graphify_refresh.OUTPUT_DIR
    output.mkdir()
    (output / "graph.html").write_text("<html></html>", encoding="utf-8")
    (output / "GRAPH_REPORT.md").write_text("# Report\n", encoding="utf-8")
    graph = {
        "directed": True,
        "built_at_commit": "checkpoint",
        "nodes": [
            {"source_file": f"packages/{package}/src/module.py"}
            for package in ("contracts", "server", "client")
        ],
        "links": [],
    }
    (output / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    monkeypatch.setattr(graphify_refresh, "head_commit", lambda _root: "checkpoint")
    fingerprint, file_count = graphify_refresh.source_fingerprint(repository)
    (repository / graphify_refresh.STATE_FILE).write_text(
        json.dumps({"fingerprint": fingerprint, "file_count": file_count}),
        encoding="utf-8",
    )

    graphify_refresh.check_state(repository, require_local_graph=True)

    graph["directed"] = False
    (output / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(graphify_refresh.GraphifyToolError, match="gerichtet"):
        graphify_refresh.check_state(repository, require_local_graph=True)

    graph["directed"] = True
    graph["nodes"] = graph["nodes"][:-1]
    (output / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(graphify_refresh.GraphifyToolError, match="client"):
        graphify_refresh.check_state(repository, require_local_graph=True)
