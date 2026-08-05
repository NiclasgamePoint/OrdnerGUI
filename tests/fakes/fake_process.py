"""Deterministic subprocess replacement used by process tests."""

from __future__ import annotations


class FakeProcess:
    def __init__(self, returncode: int | None = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, timeout=None):
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
