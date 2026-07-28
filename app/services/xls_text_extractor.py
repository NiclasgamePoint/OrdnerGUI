from __future__ import annotations

import argparse
from pathlib import Path
import sys

import xlrd

from app.core.process_support import suppress_windows_crash_dialogs


def extract_xls_text(filepath: Path, maximum_characters: int) -> str:
    """Extract legacy XLS text inside an isolatable helper process."""
    workbook = xlrd.open_workbook(str(filepath), on_demand=True)
    try:
        parts: list[str] = []
        length = 0
        for worksheet in workbook.sheets():
            parts.append(worksheet.name)
            for row_index in range(worksheet.nrows):
                values = []
                for column_index in range(worksheet.ncols):
                    value = worksheet.cell_value(row_index, column_index)
                    if value != "":
                        values.append(str(value))
                if values:
                    line = "\t".join(values)
                    parts.append(line)
                    length += len(line)
                if length >= maximum_characters:
                    return "\n".join(parts)[:maximum_characters]
        return "\n".join(parts)[:maximum_characters]
    finally:
        workbook.release_resources()


def main() -> int:
    suppress_windows_crash_dialogs()
    parser = argparse.ArgumentParser()
    parser.add_argument("filepath", type=Path)
    parser.add_argument("--maximum-characters", type=int, required=True)
    arguments = parser.parse_args()
    sys.stdout.write(
        extract_xls_text(arguments.filepath, arguments.maximum_characters)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
