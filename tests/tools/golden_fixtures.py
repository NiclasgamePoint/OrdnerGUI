from __future__ import annotations

import json
from pathlib import Path
import sqlite3


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_path(relative: str) -> Path:
    path = (FIXTURES / relative).resolve()
    if not path.is_relative_to(FIXTURES.resolve()) or not path.is_file():
        raise FileNotFoundError(relative)
    return path


def load_json_fixture(relative: str) -> dict[str, object]:
    value = json.loads(fixture_path(relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Golden fixture must contain a JSON object: {relative}")
    return value


def materialize_sqlite_fixture(relative: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination)
    try:
        connection.executescript(fixture_path(relative).read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    return destination
