"""Immutable values shared by the preview application service and Qt views."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PreviewKind(StrEnum):
    PDF = "pdf"
    SPREADSHEET = "spreadsheet"
    TEXT = "text"
    WORD = "word"
    IMAGE = "image"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class PreviewSelection:
    path: Path
    kind: PreviewKind


@dataclass(frozen=True, slots=True)
class ConversionOutcome:
    """Result of a preview-only conversion.

    Exactly one of ``path`` and ``text`` is populated.  The source document is
    never modified; generated files live in a disposable directory or the
    client preview cache.
    """

    action: str
    tool: str
    converted: bool
    path: Path | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.text is None):
            raise ValueError("conversion outcome requires exactly one payload")


@dataclass(frozen=True, slots=True)
class SheetPreview:
    name: str
    values: tuple[tuple[object, ...], ...]
    total_rows: int
    total_columns: int
    maximum_rows: int
    maximum_columns: int

    @property
    def displayed_rows(self) -> int:
        return len(self.values)

    @property
    def displayed_columns(self) -> int:
        return max((len(row) for row in self.values), default=0)

    @property
    def truncated(self) -> bool:
        return (
            self.total_rows > self.maximum_rows
            or self.total_columns > self.maximum_columns
        )
