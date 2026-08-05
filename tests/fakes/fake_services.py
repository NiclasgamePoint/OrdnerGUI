"""Small configurable service doubles."""

from __future__ import annotations


class RecordingService:
    def __init__(self, result=None):
        self.result = result
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result
