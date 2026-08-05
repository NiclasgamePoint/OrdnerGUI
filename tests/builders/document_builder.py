"""Builder for document files and their metadata."""

from __future__ import annotations

from pathlib import Path


class DocumentBuilder:
    def __init__(self, root: Path):
        self.root = root
        self.relative_path = Path("2026") / "document.txt"
        self.content = "Testinhalt"

    def at(self, relative_path: str) -> "DocumentBuilder":
        self.relative_path = Path(relative_path)
        return self

    def containing(self, content: str) -> "DocumentBuilder":
        self.content = content
        return self

    def build(self) -> Path:
        path = self.root / self.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.content, encoding="utf-8")
        return path
