"""Client-only configuration and platform defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import os
from pathlib import Path
import sys
from typing import Mapping

from .application.paths import SourceMapping


class ClientTheme(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True)
class ResolvedClientConfiguration:
    settings: "ClientSettings"
    sources: Mapping[str, str]


def default_data_root() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "PapaGUI"


@dataclass(frozen=True, slots=True)
class ClientSettings:
    server_url: str = "http://127.0.0.1:8765"
    data_root: Path = field(default_factory=default_data_root)
    api_token: str = ""
    source_mappings: tuple[SourceMapping, ...] = ()
    timeout_seconds: float = 15.0
    sync_interval_seconds: int = 900
    theme: ClientTheme = ClientTheme.SYSTEM

    def __post_init__(self) -> None:
        # An empty URL is a supported pre-onboarding state.  The settings
        # presenter refuses to save that state, while the rest of the client
        # can still start from its last valid local generation.
        if self.server_url and not self.server_url.startswith(("http://", "https://")):
            raise ValueError("server_url must use HTTP or HTTPS")
        if not 900 <= self.sync_interval_seconds <= 172_800:
            raise ValueError("sync interval must be between 15 minutes and 48 hours")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not isinstance(self.theme, ClientTheme):
            try:
                object.__setattr__(self, "theme", ClientTheme(str(self.theme)))
            except ValueError as exc:
                raise ValueError("theme must be system, light, or dark") from exc

    @property
    def cache_root(self) -> Path:
        """Root containing the active pointer and the ``generations`` directory."""
        return self.data_root

    @property
    def outbox_path(self) -> Path:
        return self.data_root / "customer-outbox.db"

    @property
    def search_history_path(self) -> Path:
        return self.data_root / "search-history.json"

    @property
    def legacy_outbox_paths(self) -> tuple[Path, ...]:
        return (
            self.data_root / "customer-overlay.db",
            self.data_root / "customer-offline-queue.db",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "server_url": self.server_url,
            "api_token": self.api_token,
            "source_mappings": {
                mapping.source_id: mapping.to_dict() for mapping in self.source_mappings
            },
            "timeout_seconds": self.timeout_seconds,
            "sync_interval_seconds": self.sync_interval_seconds,
            "theme": self.theme.value,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, object],
        *,
        data_root: Path | None = None,
    ) -> "ClientSettings":
        raw_mappings = payload.get("source_mappings", {})
        if not isinstance(raw_mappings, Mapping):
            raise ValueError("source_mappings must be an object")
        mappings = tuple(
            SourceMapping.from_dict(str(source_id), roots)
            for source_id, roots in raw_mappings.items()
            if isinstance(roots, Mapping)
        )
        return cls(
            server_url=str(payload.get("server_url", "http://127.0.0.1:8765")),
            data_root=data_root or default_data_root(),
            api_token=str(payload.get("api_token", "")),
            source_mappings=mappings,
            timeout_seconds=float(payload.get("timeout_seconds", 15)),
            sync_interval_seconds=int(payload.get("sync_interval_seconds", 900)),
            theme=ClientTheme(str(payload.get("theme", ClientTheme.SYSTEM.value))),
        )

    def with_environment(
        self, environment: Mapping[str, str] | None = None
    ) -> ResolvedClientConfiguration:
        values = os.environ if environment is None else environment
        payload = self.to_dict()
        sources = {name: "persisted" for name in payload}
        environment_fields = {
            "server_url": "PAPAGUI_INDEX_SERVER_URL",
            "api_token": "PAPAGUI_API_TOKEN",
            "timeout_seconds": "PAPAGUI_API_TIMEOUT_SECONDS",
            "sync_interval_seconds": "PAPAGUI_SYNC_INTERVAL_SECONDS",
            "theme": "PAPAGUI_CLIENT_THEME",
        }
        for field_name, variable in environment_fields.items():
            if variable in values:
                payload[field_name] = values[variable]
                sources[field_name] = f"environment:{variable}"
        if "PAPAGUI_SOURCE_MAPPINGS" in values:
            try:
                mappings = json.loads(values["PAPAGUI_SOURCE_MAPPINGS"])
            except ValueError as exc:
                raise ValueError("PAPAGUI_SOURCE_MAPPINGS must be valid JSON") from exc
            if not isinstance(mappings, dict):
                raise ValueError("PAPAGUI_SOURCE_MAPPINGS must be a JSON object")
            payload["source_mappings"] = mappings
            sources["source_mappings"] = "environment:PAPAGUI_SOURCE_MAPPINGS"
        data_root = self.data_root
        if values.get("PAPAGUI_CLIENT_DATA_ROOT"):
            data_root = Path(values["PAPAGUI_CLIENT_DATA_ROOT"])
            sources["data_root"] = "environment:PAPAGUI_CLIENT_DATA_ROOT"
        return ResolvedClientConfiguration(
            ClientSettings.from_dict(payload, data_root=data_root), sources
        )

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> ClientSettings:
        return cls().with_environment(environment).settings
