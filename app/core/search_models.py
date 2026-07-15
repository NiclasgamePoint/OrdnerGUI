from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings

from app.core.config import SETTINGS_APP, SETTINGS_ORG


@dataclass(frozen=True)
class SearchFilters:
    domain_folder: str = ""
    year: str = ""
    file_type: str = ""

    @property
    def active(self) -> bool:
        return bool(self.domain_folder or self.year or self.file_type)


@dataclass(frozen=True)
class SearchPage:
    items: list[dict]
    total: int
    page: int
    page_size: int

    @property
    def page_count(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


class SearchHistory:
    """Small persisted MRU list used by the search completer."""

    KEY = "search/history"

    def __init__(self, maximum: int = 20):
        self.maximum = maximum
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

    def entries(self) -> list[str]:
        raw = self.settings.value(self.KEY, [])
        if isinstance(raw, str):
            raw = [raw]
        return [str(value) for value in raw if str(value).strip()]

    def add(self, query: str) -> list[str]:
        normalized = query.strip()
        if not normalized:
            return self.entries()
        values = [value for value in self.entries() if value.casefold() != normalized.casefold()]
        values.insert(0, normalized)
        values = values[: self.maximum]
        self.settings.setValue(self.KEY, values)
        return values

    def clear(self):
        self.settings.remove(self.KEY)
