"""Command-line entry point for the independent server product."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import threading

import uvicorn

from papagui_server.api import create_app
from papagui_server.composition import (
    RuntimeConfiguration,
    build_container,
)


RESTART_EXIT_CODE = 75


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI headless server")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="API und Indexscheduler starten")
    _runtime_arguments(serve)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-run-on-start", action="store_true")

    once = subparsers.add_parser("once", help="Einen Indexlauf ausführen")
    _runtime_arguments(once)
    once.add_argument("--full-rebuild", action="store_true")

    openapi = subparsers.add_parser(
        "openapi", help="OpenAPI-Dokument als JSON ausgeben"
    )
    _runtime_arguments(openapi)
    openapi.add_argument("--output", type=Path)
    return parser


def main(arguments: list[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not raw_arguments:
        raw_arguments = ["serve"]
    try:
        parsed = build_parser().parse_args(raw_arguments)
    except (OSError, ValueError) as error:
        print(f"PapaGUI-Server konnte nicht konfiguriert werden: {error}", file=sys.stderr)
        return 2
    command = parsed.command
    configuration = _configuration(parsed)
    try:
        container = build_container(configuration)
    except (OSError, ValueError) as error:
        print(f"PapaGUI-Server konnte nicht gestartet werden: {error}", file=sys.stderr)
        return 2
    if command == "once":
        return container.coordinator.run_once(full_rebuild=parsed.full_rebuild)
    if command == "openapi":
        app = create_app(container, manage_lifecycle=False)
        encoded = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True)
        if parsed.output is not None:
            parsed.output.parent.mkdir(parents=True, exist_ok=True)
            parsed.output.write_text(encoded + "\n", encoding="utf-8")
        else:
            print(encoded)
        return 0

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(
        container,
        manage_lifecycle=True,
        run_on_start=not parsed.no_run_on_start,
    )
    config = uvicorn.Config(app, host=parsed.host, port=parsed.port, log_level="info")
    server = uvicorn.Server(config)

    def observe_restart() -> None:
        while not server.should_exit:
            if container.coordinator.restart_requested:
                server.should_exit = True
                break
            container.coordinator.wait_for_restart(0.25)

    observer = threading.Thread(
        target=observe_restart, name="papagui-restart-observer", daemon=True
    )
    observer.start()
    server.run()
    return RESTART_EXIT_CODE if container.coordinator.restart_requested else 0


def _runtime_arguments(parser: argparse.ArgumentParser) -> None:
    defaults = RuntimeConfiguration.from_environment()
    parser.add_argument("--source", type=Path, default=defaults.source_path)
    parser.add_argument("--data", type=Path, default=defaults.data_path)
    parser.add_argument("--config", type=Path, default=defaults.config_path)
    parser.add_argument("--source-id", default=defaults.source_id)
    parser.add_argument(
        "--interval-seconds", type=int, default=defaults.default_interval_seconds
    )
    parser.add_argument(
        "--initialize-source-identity",
        action=argparse.BooleanOptionalAction,
        default=defaults.initialize_source_identity,
        help="Quellidentität beim ersten nicht-leeren Mount initialisieren",
    )


def _configuration(parsed: argparse.Namespace) -> RuntimeConfiguration:
    environment = RuntimeConfiguration.from_environment()
    return RuntimeConfiguration(
        source_path=parsed.source,
        data_path=parsed.data,
        config_path=parsed.config,
        source_id=parsed.source_id,
        client_token=environment.client_token,
        allow_insecure_no_client_token=environment.allow_insecure_no_client_token,
        default_interval_seconds=parsed.interval_seconds,
        initialize_source_identity=parsed.initialize_source_identity,
    )


if __name__ == "__main__":
    raise SystemExit(main())
