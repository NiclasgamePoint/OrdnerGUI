"""Qt-independent view models for desktop client preferences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from papagui_client.application.paths import SourceMapping
from papagui_client.config import ClientSettings, ClientTheme


@dataclass(frozen=True, slots=True)
class SourceMappingViewModel:
    source_id: str
    windows: str = ""
    macos: str = ""
    linux: str = ""


@dataclass(frozen=True, slots=True)
class ClientSettingsViewModel:
    server_url: str
    api_token: str
    mappings: tuple[SourceMappingViewModel, ...]
    interval_value: int
    interval_unit: str
    theme: str
    environment_overrides: frozenset[str]
    onboarding_required: bool


class ClientSettingsPresenter:
    MIN_INTERVAL_SECONDS = 900
    MAX_INTERVAL_SECONDS = 172_800

    def present(
        self,
        settings: ClientSettings,
        sources: Mapping[str, str] | None = None,
    ) -> ClientSettingsViewModel:
        value, unit = self.interval_fields(settings.sync_interval_seconds)
        overrides = frozenset(
            name
            for name, source in (sources or {}).items()
            if source.startswith("environment:")
        )
        mappings = tuple(
            SourceMappingViewModel(
                mapping.source_id,
                mapping.windows or "",
                mapping.macos or "",
                mapping.linux or "",
            )
            for mapping in sorted(settings.source_mappings, key=lambda item: item.source_id)
        )
        return ClientSettingsViewModel(
            server_url=settings.server_url,
            api_token=settings.api_token,
            mappings=mappings,
            interval_value=value,
            interval_unit=unit,
            theme=settings.theme.value,
            environment_overrides=overrides,
            onboarding_required=not settings.server_url.strip() or not mappings,
        )

    def build(
        self,
        *,
        current: ClientSettings,
        server_url: str,
        api_token: str,
        mappings: Iterable[SourceMappingViewModel],
        interval_value: int,
        interval_unit: str,
        theme: str,
    ) -> ClientSettings:
        normalized_url = server_url.strip()
        if not normalized_url:
            raise ValueError("Die Server-URL darf nicht leer sein")
        normalized = []
        seen = set()
        for row in mappings:
            source_id = row.source_id.strip()
            if not source_id:
                if any((row.windows.strip(), row.macos.strip(), row.linux.strip())):
                    raise ValueError("Jede Pfadzuordnung benötigt eine source_id")
                continue
            if source_id in seen:
                raise ValueError(f"source_id ist doppelt vorhanden: {source_id}")
            seen.add(source_id)
            if not any((row.windows.strip(), row.macos.strip(), row.linux.strip())):
                raise ValueError(f"Für {source_id} fehlt mindestens ein lokaler Pfad")
            normalized.append(
                SourceMapping(
                    source_id,
                    windows=row.windows.strip() or None,
                    macos=row.macos.strip() or None,
                    linux=row.linux.strip() or None,
                )
            )
        if not normalized:
            raise ValueError("Mindestens eine source_id-Pfadzuordnung ist erforderlich")
        return ClientSettings(
            server_url=normalized_url,
            data_root=current.data_root,
            api_token=api_token,
            source_mappings=tuple(normalized),
            timeout_seconds=current.timeout_seconds,
            sync_interval_seconds=self.interval_seconds(interval_value, interval_unit),
            theme=ClientTheme(theme),
        )

    @classmethod
    def interval_fields(cls, seconds: int) -> tuple[int, str]:
        seconds = max(cls.MIN_INTERVAL_SECONDS, min(cls.MAX_INTERVAL_SECONDS, seconds))
        if seconds % 3600 == 0:
            return seconds // 3600, "Stunden"
        return seconds // 60, "Minuten"

    @classmethod
    def interval_seconds(cls, value: int, unit: str) -> int:
        if unit not in {"Minuten", "Stunden"}:
            raise ValueError("Unbekannte Intervalleinheit")
        seconds = value * (3600 if unit == "Stunden" else 60)
        if not cls.MIN_INTERVAL_SECONDS <= seconds <= cls.MAX_INTERVAL_SECONDS:
            raise ValueError("Intervall muss zwischen 15 Minuten und 48 Stunden liegen")
        return seconds
