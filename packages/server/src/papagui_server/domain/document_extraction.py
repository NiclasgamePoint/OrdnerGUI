"""Portable, versioned extraction values; large artifacts stay on the server."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


PARSER_VERSION = "structured-2"
EXTRACTION_STATUSES = frozenset(
    {
        "ok",
        "partial",
        "no_text",
        "unsupported",
        "too_large",
        "encrypted",
        "tool_missing",
        "timeout",
        "error",
    }
)


@dataclass(frozen=True, slots=True)
class ExtractionBlock:
    text: str
    kind: str = "paragraph"
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    section: str = ""
    sheet: str = ""
    cell: str = ""
    confidence: float | None = None
    method: str = "text"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExtractionPage:
    number: int
    status: str = "ok"
    reason: str = ""
    width: float | None = None
    height: float | None = None
    method: str = "text"


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    text: str = ""
    blocks: tuple[ExtractionBlock, ...] = ()
    status: str = "no_text"
    reason: str = ""
    pages_total: int | None = None
    pages_processed: int = 0
    parser_version: str = PARSER_VERSION
    content_hash: str = ""
    version_hash: str = ""
    artifact_hash: str = ""
    pages: tuple[ExtractionPage, ...] = ()
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.status not in EXTRACTION_STATUSES:
            raise ValueError("Invalid extraction status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExtractionResult":
        data = {key: item for key, item in value.items() if key in cls.__dataclass_fields__}
        data["blocks"] = tuple(
            ExtractionBlock(
                **{
                    **block,
                    "bbox": tuple(block["bbox"]) if block.get("bbox") else None,
                }
            )
            for block in data.get("blocks", ())
        )
        data["pages"] = tuple(ExtractionPage(**page) for page in data.get("pages", ()))
        return cls(**data)


def bounded_result(
    blocks: list[ExtractionBlock],
    maximum: int,
    *,
    status: str = "ok",
    reason: str = "",
    pages_total: int | None = None,
    pages_processed: int = 0,
    pages: tuple[ExtractionPage, ...] = (),
) -> ExtractionResult:
    """Apply the same character budget to text and layout, preserving their order."""
    selected: list[ExtractionBlock] = []
    length = 0
    truncated = False
    for block in blocks:
        if not block.text.strip():
            continue
        remaining = maximum - length - bool(selected)
        if remaining <= 0:
            truncated = True
            break
        text = block.text[:remaining]
        selected.append(replace(block, text=text))
        length += len(text) + (len(selected) > 1)
        if len(text) < len(block.text):
            truncated = True
            break
    text = "\n".join(block.text for block in selected)
    if truncated:
        status, reason = "partial", "character_budget"
    elif not text and status == "ok":
        status, reason = "no_text", reason or "empty_document"
    return ExtractionResult(
        text=text,
        blocks=tuple(selected),
        status=status,
        reason=reason,
        pages_total=pages_total,
        pages_processed=pages_processed,
        pages=pages,
    )
