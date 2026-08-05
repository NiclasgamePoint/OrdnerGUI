"""Process fixture helpers."""

from __future__ import annotations

import subprocess

from tests.base.test_case import PapaGuiTestCase
from tests.fakes.fake_process import FakeProcess


class ProcessTestCase(PapaGuiTestCase):
    def completed_process(
        self, returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def fake_process(self, **kwargs) -> FakeProcess:
        return FakeProcess(**kwargs)
