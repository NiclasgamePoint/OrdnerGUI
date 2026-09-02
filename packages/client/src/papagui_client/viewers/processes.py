"""Cancellable subprocess adapter used by optional preview converters."""

from __future__ import annotations

import subprocess
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=dict(environment) if environment is not None else None,
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
