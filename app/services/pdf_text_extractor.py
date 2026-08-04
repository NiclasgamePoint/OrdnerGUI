from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PyPDF2 import PdfReader


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filepath", type=Path)
    parser.add_argument("--maximum-characters", type=int, required=True)
    args = parser.parse_args()
    reader = PdfReader(str(args.filepath))
    parts: list[str] = []
    length = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
            length += len(text)
        if length >= args.maximum_characters:
            break
    sys.stdout.write("\n".join(parts)[: args.maximum_characters])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
