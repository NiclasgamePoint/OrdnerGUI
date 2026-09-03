#!/usr/bin/env python3
"""Generate or verify the deterministic PapaGUI Server OpenAPI snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from papagui_server.api import create_app
from papagui_server.composition import RuntimeConfiguration, build_container


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "tests" / "server" / "openapi-v2.json"


def generate() -> dict[str, object]:
    with TemporaryDirectory(prefix="papagui-openapi-") as directory:
        temporary = Path(directory)
        source = temporary / "source"
        source.mkdir()
        container = build_container(
            RuntimeConfiguration(
                source_path=source,
                data_path=temporary / "data",
                config_path=temporary / "config",
                client_token="openapi-snapshot-token",
            )
        )
        return create_app(container, manage_lifecycle=False).openapi()


def serialized(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="gegen Snapshot prüfen")
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    expected = serialized(generate())
    if arguments.check:
        try:
            current = arguments.output.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"OpenAPI-Snapshot fehlt: {exc}", file=sys.stderr)
            return 1
        if current != expected:
            print(
                "OpenAPI-Snapshot ist veraltet; "
                "`python tools/export_openapi.py` ausführen.",
                file=sys.stderr,
            )
            return 1
        print("OpenAPI-Snapshot ist aktuell.")
        return 0
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(expected, encoding="utf-8")
    print(f"OpenAPI-Snapshot geschrieben: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
