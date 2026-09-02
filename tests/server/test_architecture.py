from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER_SOURCE = ROOT / "packages" / "server" / "src"


def test_server_has_no_qt_client_or_legacy_app_imports() -> None:
    forbidden: list[tuple[Path, str]] = []
    for path in SERVER_SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                if name == "app" or name.startswith("app.") or name.startswith("PySide") or name.startswith("papagui_client"):
                    forbidden.append((path.relative_to(ROOT), name))
    assert forbidden == []


def test_server_dockerfile_copies_only_server_and_contracts_code() -> None:
    dockerfile = (ROOT / "deploy" / "server" / "Dockerfile").read_text(encoding="utf-8")
    copy_lines = [line.strip() for line in dockerfile.splitlines() if line.startswith("COPY ")]
    assert copy_lines == [
        "COPY packages/contracts ./packages/contracts",
        "COPY packages/server ./packages/server",
    ]
    assert "PySide" not in dockerfile
    assert "COPY app" not in dockerfile
