"""Command-line entry point for best-effort client generation synchronization."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.services.index_distribution import IndexGenerationClient, IndexSyncError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI Indexclient")
    parser.add_argument("--server", required=True)
    parser.add_argument("--client-root", required=True, type=Path)
    parser.add_argument("--bootstrap-from", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=5)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    client = IndexGenerationClient(
        arguments.server,
        arguments.client_root,
        token=os.getenv("PAPAGUI_API_TOKEN", ""),
        timeout_seconds=arguments.timeout_seconds,
    )
    if arguments.bootstrap_from is not None:
        client.bootstrap_from(arguments.bootstrap_from)
    try:
        changed = client.sync()
    except IndexSyncError as exc:
        if client.has_local_generation():
            print(f"Indexserver nicht erreichbar; lokale Generation bleibt aktiv: {exc}")
            return 0
        print(str(exc))
        return 1
    print("Neue Indexgeneration aktiviert." if changed else "Indexgeneration ist aktuell.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
