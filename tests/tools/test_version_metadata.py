from __future__ import annotations

import json
from pathlib import Path

import papagui_client
import papagui_contracts
import papagui_server


ROOT = Path(__file__).resolve().parents[2]


def test_initial_split_versions_are_independent_and_consistent() -> None:
    assert papagui_contracts.__version__ == "0.4.4"
    assert papagui_server.__version__ == "0.4.4"
    assert papagui_client.__version__ == "0.4.4"


def test_server_deployment_metadata_matches_server_version() -> None:
    version = papagui_server.__version__
    paths = (
        ROOT / "Dockerfile",
        ROOT / "compose.yaml",
        ROOT / "deploy" / "server" / "Dockerfile",
        ROOT / "deploy" / "server" / "compose.yaml",
        ROOT / "deploy" / "server" / "docker-bake.hcl",
        ROOT / "tools" / "docker_smoke.sh",
    )
    for path in paths:
        assert f"papagui-server:{version}" in path.read_text(encoding="utf-8") or (
            path.name == "Dockerfile"
            and f'org.opencontainers.image.version="{version}"'
            in path.read_text(encoding="utf-8")
        ), path


def test_client_build_matrix_matches_client_version() -> None:
    matrix = json.loads(
        (ROOT / "packaging" / "client" / "build-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    assert matrix["version"] == papagui_client.__version__
    workflow = (ROOT / ".github" / "workflows" / "client-artifacts.yml").read_text(
        encoding="utf-8"
    )
    assert f"papagui-client-{papagui_client.__version__}-" in workflow
