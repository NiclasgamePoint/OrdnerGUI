"""JSON persistence adapter for resumable index-run state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from papagui_server.adapters.generations import atomic_json


class JsonRunStateRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, payload: dict[str, Any]) -> None:
        atomic_json(self.path, payload)
