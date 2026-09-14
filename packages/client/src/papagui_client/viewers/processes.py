"""Cancellable subprocess adapter used by optional preview converters."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Mapping, Sequence

from .ports import CancelCheck


class PollingCommandRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        environment: Mapping[str, str] | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=dict(environment) if environment is not None else None,
            # A windowed Qt parent otherwise opens a console for each helper,
            # including both the Office capability probe and the PDF export.
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        deadline = time.monotonic() + timeout
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                return subprocess.CompletedProcess(
                    list(command), process.returncode, stdout, stderr
                )
            except subprocess.TimeoutExpired:
                if should_cancel is not None and should_cancel():
                    self._terminate(process)
                    raise InterruptedError("Konvertierung wurde abgebrochen")
                if time.monotonic() >= deadline:
                    self._terminate(process)
                    raise subprocess.TimeoutExpired(list(command), timeout)

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        process.terminate()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
