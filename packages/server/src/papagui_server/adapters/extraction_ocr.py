"""Tesseract TSV parsing with paragraph boundaries and stable coordinates."""

from __future__ import annotations

from collections import OrderedDict
import csv
from io import StringIO

from papagui_server.domain.document_extraction import ExtractionBlock


def tsv_blocks(data: bytes, *, page: int, scale: float = 1.0) -> list[ExtractionBlock]:
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if not text.startswith("level\t"):
        # Compatibility with injected runners and older text-only tools.
        return [ExtractionBlock(text, page=page, method="ocr")]
    groups: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
    for row in csv.DictReader(StringIO(text), delimiter="\t"):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        try:
            values = {key: float(row[key]) for key in ("left", "top", "width", "height", "conf")}
        except (KeyError, TypeError, ValueError):
            continue
        groups.setdefault((row.get("block_num", ""), row.get("par_num", "")), []).append(
            {**row, **values}
        )
    blocks = []
    for words in groups.values():
        lines: OrderedDict[str, list[str]] = OrderedDict()
        for word in words:
            lines.setdefault(word.get("line_num", ""), []).append(word["text"])
        confidence = [word["conf"] for word in words if word["conf"] >= 0]
        blocks.append(
            ExtractionBlock(
                "\n".join(" ".join(line) for line in lines.values()),
                page=page,
                bbox=(
                    min(word["left"] for word in words) * scale,
                    min(word["top"] for word in words) * scale,
                    max(word["left"] + word["width"] for word in words) * scale,
                    max(word["top"] + word["height"] for word in words) * scale,
                ),
                confidence=sum(confidence) / (100 * len(confidence)) if confidence else None,
                method="tesseract_tsv",
            )
        )
    return blocks
