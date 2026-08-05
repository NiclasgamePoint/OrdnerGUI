"""Synchronous worker doubles for GUI tests."""

from __future__ import annotations


class ImmediateWorker:
    def __init__(self, result=None):
        self.result = result
        self.started = False

    def start(self) -> None:
        self.started = True

    def isRunning(self) -> bool:
        return False
