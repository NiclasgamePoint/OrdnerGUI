"""Small presentation-only values retained by the v0.4.1 visual shell.

The types in this module deliberately contain no database or indexing logic.
They allow the restored widgets to keep their original public API while the
actual data continues to flow through the 0.4.2 client application services.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QSettings


SETTINGS_ORG = "PapaGUI"
SETTINGS_APP = "UI"


class SearchSort(str, Enum):
    RELEVANCE = "relevance"
    DATE = "date"
    ALPHABETICAL = "alphabetical"


@dataclass(frozen=True, slots=True)
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
    """Persist visual search preferences without coupling to server state."""

    SORT_KEY = "search/sort_order"
    SUBFOLDERS_KEY = "search/include_subfolders"

    def __init__(self) -> None:
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

    def load(self) -> tuple[SearchSort, bool]:
        raw_sort = str(self.settings.value(self.SORT_KEY, SearchSort.RELEVANCE.value))
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

    def save(self, sort_order: SearchSort | str, include_subfolders: bool) -> None:
        try:
            normalized_sort = SearchSort(sort_order)
        except ValueError:
            normalized_sort = SearchSort.RELEVANCE
        self.settings.setValue(self.SORT_KEY, normalized_sort.value)
        self.settings.setValue(self.SUBFOLDERS_KEY, bool(include_subfolders))


@dataclass(frozen=True, slots=True)
class ApplicationStatistics:
    customer_count: int = 0
    contact_count: int = 0
    project_count: int = 0
    service_count: int = 0
    file_count: int = 0
    total_file_size: int = 0
    content_count: int = 0
    pending_recognition_count: int = 0
    last_indexed_at: str = ""
    last_index_duration_seconds: float = 0.0
