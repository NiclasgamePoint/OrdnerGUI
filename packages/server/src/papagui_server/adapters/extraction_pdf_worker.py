"""Isolated PDF layout reader. Emits bounded JSON, never exception text or logs."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median
import sys

from papagui_server.domain.document_extraction import (
    ExtractionBlock,
    ExtractionPage,
    bounded_result,
)


def read_pdf(path: Path, maximum: int, max_pages: int):
    import pdfplumber

    blocks: list[ExtractionBlock] = []
    pages: list[ExtractionPage] = []
    length = 0
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for number, page in enumerate(pdf.pages[:max_pages], 1):
            words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
            paragraphs = _paragraphs(words)
            for words_in_block in paragraphs:
                value = " ".join(str(word["text"]) for word in words_in_block).replace(" \n", "\n")
                bbox = (
                    min(float(word["x0"]) for word in words_in_block),
                    min(float(word["top"]) for word in words_in_block),
                    max(float(word["x1"]) for word in words_in_block),
                    max(float(word["bottom"]) for word in words_in_block),
                )
                blocks.append(ExtractionBlock(value, page=number, bbox=bbox, method="pdfplumber"))
                length += len(value) + 1
                if length > maximum:
                    break
            pages.append(
                ExtractionPage(
                    number,
                    "ok" if words else "no_text",
                    width=float(page.width),
                    height=float(page.height),
                    method="pdfplumber",
                )
            )
            page.close()
            if length > maximum:
                break
        partial = len(pages) < total
        return bounded_result(
            blocks,
            maximum,
            status="partial" if partial else "ok",
            reason="page_budget" if partial else "",
            pages_total=total,
            pages_processed=len(pages),
            pages=tuple(pages),
        )


def _paragraphs(words: list[dict]) -> list[list[dict]]:
    """Preserve whitespace gutters before grouping aligned lines vertically."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        if not lines or abs(float(word["top"]) - float(lines[-1][0]["top"])) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    paragraphs: list[list[dict]] = []
    for line in lines:
        line.sort(key=lambda word: float(word["x0"]))
        height = median(float(word["bottom"]) - float(word["top"]) for word in line)
        segments: list[list[dict]] = []
        for word in line:
            if not segments or float(word["x0"]) - float(segments[-1][-1]["x1"]) > max(
                24, height * 3
            ):
                segments.append([word])
            else:
                segments[-1].append(word)
        for segment in segments:
            top = min(float(word["top"]) for word in segment)
            left = float(segment[0]["x0"])
            candidates = []
            for index, paragraph in enumerate(paragraphs):
                bottom = max(float(word["bottom"]) for word in paragraph)
                # Same-row segments remain separate. A line may continue a prior
                # column even when another column was processed in between.
                gap = top - bottom
                if -1 <= gap <= max(7, height * 0.85) and abs(
                    left - float(paragraph[0]["x0"])
                ) <= max(12, height):
                    candidates.append((gap, index))
            if candidates:
                _, index = min(candidates)
                paragraphs[index].extend(
                    [{**segment[0], "text": "\n" + segment[0]["text"]}, *segment[1:]]
                )
            else:
                paragraphs.append(segment)
    return paragraphs


def main() -> None:
    try:
        # Linux workers are disposable and bounded independently of server RAM.
        try:
            import resource

            memory_mb = max(128, min(2048, int(sys.argv[4]) if len(sys.argv) > 4 else 768))
            resource.setrlimit(
                resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024)
            )
        except (ImportError, ValueError, OSError):
            pass
        result = read_pdf(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
        payload = result.to_dict()
    except ModuleNotFoundError:
        payload = {"status": "tool_missing", "reason": "pdf_layout_tool_missing"}
    except MemoryError:
        payload = {"status": "too_large", "reason": "memory_budget"}
    except Exception as exc:
        encrypted = (
            "password" in type(exc).__name__.casefold()
            or "encrypt" in type(exc).__name__.casefold()
        )
        payload = {
            "status": "encrypted" if encrypted else "error",
            "reason": "encrypted_document" if encrypted else "invalid_pdf",
        }
    # ASCII JSON escapes preserve all Unicode through Windows legacy code pages.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))


if __name__ == "__main__":
    main()
