"""Fluent filesystem fixture builder."""

from __future__ import annotations

from pathlib import Path


class FilesystemBuilder:
    def __init__(self, root: Path):
        self.root = root

    def directory(self, relative_path: str) -> "FilesystemBuilder":
        (self.root / relative_path).mkdir(parents=True, exist_ok=True)
        return self

    def file(self, relative_path: str, content: str = "") -> "FilesystemBuilder":
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self
