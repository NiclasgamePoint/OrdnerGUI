"""Standalone tray entrypoint for remote server control."""

from __future__ import annotations

import argparse
import sys

from papagui_client.composition import build_tray


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI server-control tray")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--update-probe", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    from papagui_client.updates.runtime import try_launch, probe_startup

    if arguments.update_probe:
        return probe_startup()
    managed = try_launch("tray", list(sys.argv[1:] if argv is None else argv))
    if managed is not None:
        return managed
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
