"""Builder for compact index-record fixtures."""

from __future__ import annotations


class IndexRecordBuilder:
    def __init__(self):
        self.values = {"path": "/data/document.txt", "year": 2026, "size": 0}

    def with_value(self, key: str, value) -> "IndexRecordBuilder":
        self.values[key] = value
        return self

    def build(self) -> dict:
        return dict(self.values)
