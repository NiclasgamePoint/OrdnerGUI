"""Reusable base class for tests that need an isolated filesystem."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class PapaGuiTestCase(unittest.TestCase):
    """Give each test a private temporary directory with automatic cleanup."""

    def setUp(self) -> None:
        super().setUp()
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temporary_directory.name)
        self.addCleanup(self._temporary_directory.cleanup)

    def make_file(self, relative_path: str, content: str = "") -> Path:
        path = self.temp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
