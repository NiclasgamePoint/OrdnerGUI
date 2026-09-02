#!/usr/bin/env python3
"""Refresh PapaGUI's ignored Graphify output and verify its source fingerprint.

The graph itself intentionally remains local.  A small, committed state file
records which repository inputs were used, allowing CI to detect stale graphs
without checking the generated HTML/JSON into Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


STATE_FILE = ".graphify-source-state.json"
OUTPUT_DIR = "graphify-out"
PACKAGE_NAME = "graphifyy"


class GraphifyToolError(RuntimeError):
    """Raised when Graphify cannot be located or completed successfully."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(root: Path, *command: str, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=root,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def graphify_executable(root: Path) -> str:
    configured = os.environ.get("GRAPHIFY_BIN", "").strip()
    candidates = [
        configured,
        str(root / ".venv" / "bin" / "graphify"),
        str(root / ".venv" / "Scripts" / "graphify.exe"),
        shutil.which("graphify") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise GraphifyToolError(
        "Graphify wurde nicht gefunden. Installieren Sie das isolierte Dev-Tool "
        "mit `uv tool install graphifyy` oder `pipx install graphifyy`."
    )


def graphify_version(root: Path, executable: str) -> str:
    output = _run(root, executable, "--version", capture=True)
    parts = output.split()
    if len(parts) < 2:
        raise GraphifyToolError(f"Unerwartete Graphify-Version: {output!r}")
    return parts[-1]


def repository_files(root: Path) -> list[Path]:
    output = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    excluded = {STATE_FILE}
    files: list[Path] = []
    for raw_name in output.split(b"\0"):
        if not raw_name:
            continue
        relative = Path(os.fsdecode(raw_name))
        if (
            relative.as_posix() in excluded
            or relative.parts[:1] == (OUTPUT_DIR,)
            or relative.parts[:1] == (".git",)
        ):
            continue
        absolute = root / relative
        if absolute.is_file():
            files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def source_fingerprint(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = repository_files(root)
    for relative in files:
        encoded_name = relative.as_posix().encode("utf-8", errors="surrogateescape")
        content = (root / relative).read_bytes()
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), len(files)


def head_commit(root: Path) -> str:
    return _run(root, "git", "rev-parse", "HEAD", capture=True)


def graph_commit(root: Path) -> str | None:
    graph_path = root / OUTPUT_DIR / "graph.json"
    if not graph_path.is_file():
        return None
    try:
        return str(json.loads(graph_path.read_text(encoding="utf-8"))["built_at_commit"])
    except (KeyError, TypeError, ValueError):
        return None


def validate_local_graph(root: Path) -> str:
    """Validate the ignored final artifacts required before component tags."""
    output = root / OUTPUT_DIR
    required = (
        output / "graph.html",
        output / "graph.json",
        output / "GRAPH_REPORT.md",
    )
    missing = [
        path.name
        for path in required
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        raise GraphifyToolError(
            "Lokale Graphify-Ausgaben fehlen oder sind leer: " + ", ".join(missing)
        )
    try:
        graph = json.loads((output / "graph.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise GraphifyToolError("graphify-out/graph.json ist ungültig.") from exc
    if graph.get("directed") is not True:
        raise GraphifyToolError("Der lokale Abschlussgraph muss gerichtet sein.")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise GraphifyToolError("Der lokale Graph enthält keine gültige Knotenliste.")
    source_files = {
        str(node.get("source_file", "")).replace("\\", "/")
        for node in nodes
        if isinstance(node, dict)
    }
    missing_packages = [
        package
        for package in ("contracts", "server", "client")
        if not any(
            source.startswith(f"packages/{package}/") for source in source_files
        )
    ]
    if missing_packages:
        raise GraphifyToolError(
            "Der lokale Graph enthält nicht alle neuen Pakete: "
            + ", ".join(missing_packages)
        )
    built_at = graph.get("built_at_commit")
    return str(built_at) if built_at else ""


def write_state(root: Path, version: str) -> None:
    fingerprint, file_count = source_fingerprint(root)
    state = {
        "schema": 1,
        "fingerprint": fingerprint,
        "file_count": file_count,
        "graphify_version": version,
        "output_policy": "local-and-gitignored",
    }
    (root / STATE_FILE).write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def check_state(root: Path, *, require_local_graph: bool) -> None:
    state_path = root / STATE_FILE
    if not state_path.is_file():
        raise GraphifyToolError(f"{STATE_FILE} fehlt; Graphify muss aktualisiert werden.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    fingerprint, file_count = source_fingerprint(root)
    if state.get("fingerprint") != fingerprint or state.get("file_count") != file_count:
        raise GraphifyToolError(
            "Der Graphify-Quellstand ist veraltet. "
            "Führen Sie `python tools/graphify_refresh.py` aus."
        )
    if require_local_graph:
        built_at = validate_local_graph(root)
        current_head = head_commit(root)
        if built_at != current_head:
            raise GraphifyToolError(
                f"Lokaler Graph ist für {built_at or 'keinen Commit'} gebaut; "
                f"erwartet wird {current_head}."
            )


def latest_version() -> str:
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{PACKAGE_NAME}/json", timeout=10
    ) as response:
        return str(json.load(response)["info"]["version"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="nur den getrackten Quell-Fingerprint prüfen",
    )
    parser.add_argument(
        "--require-local-graph",
        action="store_true",
        help="zusätzlich graph.json und dessen Commitbezug prüfen",
    )
    parser.add_argument(
        "--record-only",
        action="store_true",
        help="nach einem manuell ausgeführten Vollauf nur den Fingerprint speichern",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="einen vollständigen lokalen Codegraphen statt eines Updates bauen",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="bewusstes Schrumpfen nach großen Umstrukturierungen erlauben",
    )
    parser.add_argument(
        "--check-latest",
        action="store_true",
        help="installierte Graphify-Version mit PyPI vergleichen",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    root = repository_root()
    try:
        if arguments.check:
            check_state(root, require_local_graph=arguments.require_local_graph)
            print("Graphify-Quellstand ist aktuell.")
            return 0

        executable = graphify_executable(root)
        version = graphify_version(root, executable)
        if arguments.check_latest:
            latest = latest_version()
            if version != latest:
                raise GraphifyToolError(
                    f"Graphify {version} ist veraltet; auf PyPI ist {latest} verfügbar."
                )
            print(f"Graphify {version} ist aktuell.")
            return 0

        if not arguments.record_only:
            if arguments.full:
                command = [
                    executable,
                    "extract",
                    str(root),
                    "--code-only",
                    "--force",
                ]
            else:
                command = [executable, "update", str(root)]
                if arguments.force:
                    command.append("--force")
            _run(root, *command)
            _run(root, executable, "export", "html")
        write_state(root, version)
        print(
            f"Graphify {version} aktualisiert; {STATE_FILE} wurde geschrieben. "
            "Dokumentänderungen benötigen zusätzlich den semantischen Skill-Lauf."
        )
        return 0
    except (GraphifyToolError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Graphify-Prüfung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
