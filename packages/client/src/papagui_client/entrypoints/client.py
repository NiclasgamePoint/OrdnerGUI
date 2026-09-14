"""PapaGUI desktop client entrypoint."""

from __future__ import annotations

import argparse
import sys

from papagui_client.application.sync import SyncError
from papagui_client.composition import build_client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI client")
    parser.add_argument("--sync-only", action="store_true", help="synchronize and exit")
    parser.add_argument("--offline", action="store_true", help="skip the startup sync")
    parser.add_argument("--update-probe", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    from papagui_client.updates.runtime import try_launch, probe_startup

    if arguments.update_probe:
        return probe_startup()
    managed = try_launch("client", list(sys.argv[1:] if argv is None else argv))
    if managed is not None:
        return managed
    container = build_client()
    if arguments.sync_only and not arguments.offline:
        try:
            result = container.sync.sync()
            if result.awaiting_generation:
                message = "Server erreichbar; noch kein fertiger Index verfügbar."
            elif result.changed:
                message = "Neue Datengeneration aktiviert: " + ", ".join(result.changed_components)
            else:
                message = "Lokale Datengeneration ist aktuell."
            print(message)
        except SyncError as exc:
            if not container.sync.has_local_data():
                print(f"Keine verwendbare lokale Generation: {exc}", file=sys.stderr)
                return 1
            print(f"Server nicht erreichbar; lokale Generation bleibt aktiv: {exc}", file=sys.stderr)
    if arguments.sync_only:
        return 0
    try:
        from papagui_client.gui.main import run_main_gui
    except ImportError:
        print("Die Qt-GUI ist nicht installiert; installieren Sie papagui-client[gui].", file=sys.stderr)
        return 2
    return run_main_gui(container, automatic_sync=not arguments.offline)


if __name__ == "__main__":
    raise SystemExit(main())
