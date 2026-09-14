"""Transport-independent client application errors."""

from __future__ import annotations

from papagui_contracts.customers import Customer


class GenerationNotReady(RuntimeError):
    """The server is reachable but has no complete published generation yet."""


class CustomerGatewayUnavailable(RuntimeError):
    pass


class CustomerGatewayConflict(RuntimeError):
    def __init__(self, current: Customer | None):
        super().__init__("customer revision conflict")
        self.current = current


class CustomerGatewayIdempotencyConflict(RuntimeError):
    """A key was reused for different semantics; this is not a data revision conflict."""
