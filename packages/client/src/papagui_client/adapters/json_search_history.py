"""Small atomic MRU store for client-side search terms."""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


class JsonSearchHistoryRepository:
    def __init__(self, path: Path, *, maximum: int = 20) -> None:
        if maximum < 1:
            raise ValueError("search history maximum must be positive")
        self.path = path.expanduser().resolve()
        self.maximum = maximum

    def entries(self) -> tuple[str, ...]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeDecodeError, ValueError):
            return ()
        raw = payload.get("entries", ()) if isinstance(payload, dict) else ()
        if not isinstance(raw, list):
            return ()
        values = []
        seen = set()
        for item in raw:
            value = str(item).strip()
            key = value.casefold()
            if value and key not in seen:
                values.append(value)
                seen.add(key)
        return tuple(values[: self.maximum])

    def add(self, query: str) -> tuple[str, ...]:
        value = query.strip()
        if not value:
            return self.entries()
        values = [item for item in self.entries() if item.casefold() != value.casefold()]
        values.insert(0, value)
        self._write(values[: self.maximum])
        return tuple(values[: self.maximum])

    def clear(self) -> None:
        self._write([])

    def _write(self, entries: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(
                    {"schema_version": 1, "entries": entries},
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
