"""Defaults for concise customer-model fixtures."""

from __future__ import annotations


class CustomerBuilder:
    def __init__(self):
        self.values = {"name": "Erika Muster", "customer_type": "Privatkunde"}

    def with_value(self, key: str, value) -> "CustomerBuilder":
        self.values[key] = value
        return self

    def build(self) -> dict:
        return dict(self.values)
