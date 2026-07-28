from __future__ import annotations

import argparse
from pathlib import Path
import sys

from app.core.process_support import suppress_windows_crash_dialogs
from app.services.document_converter import DocumentConverter


def main() -> int:
    suppress_windows_crash_dialogs()
    parser = argparse.ArgumentParser()
    parser.add_argument("filepath", type=Path)
    parser.add_argument("--maximum-characters", type=int, required=True)
    arguments = parser.parse_args()
    text = DocumentConverter().extract_legacy_doc(arguments.filepath)
    sys.stdout.write(text[: arguments.maximum_characters])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
