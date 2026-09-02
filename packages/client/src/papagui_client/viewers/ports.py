"""Ports consumed by preview conversion services."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable, Mapping, Protocol, Sequence


CancelCheck = Callable[[], bool]


class ExecutableResolver(Protocol):
    def resolve(self, name: str) -> Path | None: ...


class CommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        environment: Mapping[str, str] | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> subprocess.CompletedProcess[str]: ...
