"""Atomic JSON persistence for server-owned settings."""

from __future__ import annotations

import json
from pathlib import Path
import threading

from papagui_server.adapters.generations import atomic_json
from papagui_server.domain.models import ServerSettings


class JsonSettingsRepository:
    def __init__(self, config_path: Path, defaults: ServerSettings | None = None) -> None:
        self.path = config_path
        self.defaults = defaults or ServerSettings()
        self._lock = threading.RLock()

    def load(self) -> ServerSettings:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                # Extend only the former shipped default; custom selections remain explicit.
                old_extensions = "pdf,doc,docx,xls,xlsx,txt,csv,md,log,json,xml,yaml,yml,ini"
                if (isinstance(payload, dict) and "recognition_pipeline_enabled" not in payload
                        and payload.get("content_extensions") == old_extensions):
                    payload["content_extensions"] = ServerSettings().content_extensions
                return ServerSettings.from_mapping(payload)
            except FileNotFoundError:
                return self.defaults
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # Invalid configuration is never silently overwritten. The service
                # remains usable with explicit defaults while the file can be fixed.
                return self.defaults

    def save(self, settings: ServerSettings) -> None:
        with self._lock:
            atomic_json(self.path, settings.to_dict())
