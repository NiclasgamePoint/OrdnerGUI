"""Seed editable Windows launcher defaults without overriding saved preferences."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
from urllib.parse import urlsplit

from papagui_client.adapters.json_config import JsonClientConfigRepository
from papagui_client.config import ClientSettings, ResolvedClientConfiguration


DEFAULT_VARIABLES = (
    "PAPAGUI_INDEX_SERVER_URL", "PAPAGUI_API_TOKEN", "PAPAGUI_SOURCE_MAPPINGS",
)


def prepare(
    environment: Mapping[str, str], defaults: Sequence[str], *, seed: bool = False,
) -> ResolvedClientConfiguration:
    data_root = Path(environment["PAPAGUI_CLIENT_DATA_ROOT"])
    repository = JsonClientConfigRepository(
        Path(environment.get("PAPAGUI_CLIENT_CONFIG_PATH", data_root / "client-config.json")),
        data_root=data_root,
    )
    overrides = {name: value for name, value in environment.items() if name not in defaults}
    if repository.path.exists():
        return repository.resolve(overrides)
    # Only launcher-generated defaults are persisted. Explicit environment
    # overrides retain their precedence and remain visibly locked in the GUI.
    settings = ClientSettings(data_root=data_root).with_environment({
        name: environment[name] for name in defaults if name in environment
    }).settings
    if seed:
        repository.save(settings)
    return settings.with_environment(overrides)


def uses_local_server(settings: ClientSettings, environment: Mapping[str, str]) -> bool:
    url = urlsplit(settings.server_url)
    return (
        url.scheme == "http"
        and url.hostname in {"localhost", "127.0.0.1", "::1"}
        and (url.port or 80) == int(environment.get("PAPAGUI_API_PORT", "8765"))
        and url.path in {"", "/"}
        and not (url.query or url.fragment or url.username or url.password)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--default-variable", choices=DEFAULT_VARIABLES, action="append", default=[])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local-server", action="store_true")
    mode.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    resolved = prepare(os.environ, args.default_variable, seed=args.seed)
    if args.local_server:
        # Never send credentials or configuration JSON through shell output.
        print("true" if uses_local_server(resolved.settings, os.environ) else "false")


if __name__ == "__main__":
    main()
