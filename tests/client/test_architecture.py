from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "packages" / "client" / "src" / "papagui_client"


def imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_client_has_no_server_index_writer_or_recognition_imports():
    forbidden_fragments = (
        "papagui_server",
        "index_manager",
        "index_job",
        "customer_recognition",
        "catalog_index",
        "content_index",
    )
    violations = []
    for path in CLIENT.rglob("*.py"):
        modules = tuple(imported_modules(path))
        for module in modules:
            if any(fragment in module for fragment in forbidden_fragments):
                violations.append(f"{path.relative_to(CLIENT)} -> {module}")
            if module == "app" or module.startswith("app."):
                violations.append(f"{path.relative_to(CLIENT)} -> eager legacy import {module}")
    assert violations == []


def test_core_import_does_not_load_qt_or_legacy_app():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "packages" / "contracts" / "src"),
            str(ROOT / "packages" / "client" / "src"),
        ]
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import papagui_client.composition; "
            "assert not any(n.startswith('PySide6') for n in sys.modules); "
            "assert 'app.gui' not in sys.modules",
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
