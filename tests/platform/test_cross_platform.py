"""Platform contracts that can be verified deterministically on every runner."""

from __future__ import annotations

import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from app.core import process_support
from app.services.index_tool_resolver import IndexToolResolver


class CrossPlatformTests(unittest.TestCase):
    def test_tool_resolver_maps_all_release_platforms(self):
        cases = (
            ("win32", "AMD64", "windows-x64"),
            ("darwin", "arm64", "macos-arm64"),
            ("darwin", "x86_64", "macos-x64"),
            ("linux", "x86_64", "linux-x64"),
        )
        for operating_system, machine, expected in cases:
            with self.subTest(operating_system=operating_system, machine=machine):
                with (
                    patch("app.services.index_tool_resolver.sys.platform", operating_system),
                    patch("app.services.index_tool_resolver.platform.machine", return_value=machine),
                ):
                    self.assertEqual(IndexToolResolver.platform_tag(), expected)

    def test_resolver_uses_windows_executable_suffix(self):
        resolver = IndexToolResolver(Path("bundle"))
        with (
            patch("app.services.index_tool_resolver.sys.platform", "win32"),
            patch.object(IndexToolResolver, "platform_tag", return_value="windows-x64"),
            patch.object(Path, "is_file", return_value=True),
        ):
            resolved = resolver.resolve("pdftotext")
        self.assertEqual(PureWindowsPath(resolved).name, "pdftotext.exe")

    def test_process_support_is_noop_away_from_windows(self):
        with patch("app.core.process_support.os.name", "posix"):
            process_support.suppress_windows_crash_dialogs()
