"""Validation and persistence boundary for server settings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from papagui_server.application.ports import SettingsRepositoryPort
from papagui_server.domain.models import ServerSettings


class SettingsApplicationService:
    def __init__(
        self,
        repository: SettingsRepositoryPort,
        changed: Callable[[ServerSettings], None] | None = None,
    ) -> None:
        self._repository = repository
        self._changed = changed

    def get(self) -> ServerSettings:
        return self._repository.load()

    def update(self, values: dict[str, Any]) -> ServerSettings:
        current = self._repository.load().to_dict()
        unknown = set(values) - set(ServerSettings.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unbekannte Servereinstellung: {sorted(unknown)[0]}")
        current.update(values)
        settings = ServerSettings.from_mapping(current)
        self._repository.save(settings)
        if self._changed is not None:
            self._changed(settings)
        return settings
