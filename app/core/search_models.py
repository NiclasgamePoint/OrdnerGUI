from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from PySide6.QtCore import QSettings

from app.core.config import SETTINGS_APP, SETTINGS_ORG


class SearchSort(str, Enum):
    RELEVANCE = "relevance"
    DATE = "date"
    ALPHABETICAL = "alphabetical"


@dataclass(frozen=True)
class SearchFilters:
    domain_folder: str = ""
    year: str = ""
    file_type: str = ""
    sort_order: SearchSort = SearchSort.RELEVANCE
    include_subfolders: bool = False

    @property
    def active(self) -> bool:
        return bool(self.domain_folder or self.year or self.file_type)


class SearchPreferences:
    """Persist user-controlled search presentation options."""

    SORT_KEY = "search/sort_order"
    SUBFOLDERS_KEY = "search/include_subfolders"

    def __init__(self):
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

    def load(self) -> tuple[SearchSort, bool]:
        raw_sort = str(
            self.settings.value(self.SORT_KEY, SearchSort.RELEVANCE.value)
        )
        try:
            sort_order = SearchSort(raw_sort)
        except ValueError:
            sort_order = SearchSort.RELEVANCE
        raw_subfolders = self.settings.value(self.SUBFOLDERS_KEY, False)
        include_subfolders = (
            raw_subfolders
            if isinstance(raw_subfolders, bool)
            else str(raw_subfolders).casefold() in {"1", "true", "yes"}
        )
        return sort_order, include_subfolders

    def save(self, sort_order: SearchSort | str, include_subfolders: bool):
        try:
            normalized_sort = SearchSort(sort_order)
        except ValueError:
            normalized_sort = SearchSort.RELEVANCE
        self.settings.setValue(self.SORT_KEY, normalized_sort.value)
        self.settings.setValue(self.SUBFOLDERS_KEY, bool(include_subfolders))


@dataclass(frozen=True)
class SearchPage:
    items: list[Any]
    total: int
    page: int
    page_size: int
    coverage: "SearchCoverage | None" = None

    @property
    def page_count(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


@dataclass(frozen=True)
class SearchCoverage:
    completed_documents: int
    total_documents: int
    completed_bytes: int
    total_bytes: int
    complete: bool
    unavailable_shards: int = 0


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


class RecentCustomerHistory:
    """Persisted MRU list for customers shown on the empty search screen."""

    KEY = "search/recent_customers"

    def __init__(self, maximum: int = 5):
        self.maximum = maximum
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

    def ids(self) -> list[int]:
        raw = self.settings.value(self.KEY, [])
        if isinstance(raw, (str, int)):
            raw = [raw]
        ids = []
        seen = set()
        for value in raw:
            try:
                customer_id = int(value)
            except (TypeError, ValueError):
                continue
            if customer_id > 0 and customer_id not in seen:
                ids.append(customer_id)
                seen.add(customer_id)
        return ids[: self.maximum]

    def remember(self, customer_ids: list[int]) -> list[int]:
        new_ids = []
        seen = set()
        for value in customer_ids:
            try:
                customer_id = int(value)
            except (TypeError, ValueError):
                continue
            if customer_id > 0 and customer_id not in seen:
                new_ids.append(customer_id)
                seen.add(customer_id)
        if not new_ids:
            return self.ids()
        values = [
            customer_id
            for customer_id in self.ids()
            if customer_id not in seen
        ]
        values = [*new_ids, *values][: self.maximum]
        self.settings.setValue(self.KEY, values)
        return values

    def clear(self):
        self.settings.remove(self.KEY)
