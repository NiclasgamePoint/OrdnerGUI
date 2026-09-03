"""Standalone tray entrypoint for remote server control."""

from __future__ import annotations

import argparse
import sys

from papagui_client.composition import build_tray


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI server-control tray")
    parser.add_argument("--background", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    container = build_tray()
    try:
        from papagui_client.gui.tray import run_tray_gui
    except ImportError:
        print("Das Qt-Tray ist nicht installiert; installieren Sie papagui-client[gui].", file=sys.stderr)
        return 2
    forwarded = ["--background"] if arguments.background else []
    return run_tray_gui(container, forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
