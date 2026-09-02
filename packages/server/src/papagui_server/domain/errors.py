"""Expected application errors independent from HTTP and SQLite."""

from __future__ import annotations

from typing import Any


class ResourceNotFoundError(LookupError):
    """A requested aggregate or immutable generation does not exist."""


class ResourceBusyError(RuntimeError):
    """An exclusive server operation cannot currently be accepted."""


class AdminAuthenticationError(RuntimeError):
    """Administrative authentication failed or is not configured."""


class CustomerConflictError(RuntimeError):
    """A mutation was based on an outdated customer revision."""

    def __init__(self, current: dict[str, Any] | None):
        super().__init__("Der Kundendatensatz wurde zwischenzeitlich geändert.")
        self.current = current


class IdempotencyConflictError(RuntimeError):
    """One idempotency key was reused for another request."""

    def __init__(self) -> None:
        super().__init__("Der Idempotency-Key wurde bereits anders verwendet.")


class ProjectAssignmentConflictError(RuntimeError):
    """A portable project root is already owned by another customer."""

    def __init__(self, current_customer_id: int) -> None:
        super().__init__("Die Projektwurzel ist bereits einem anderen Kunden zugeordnet.")
        self.current_customer_id = current_customer_id


class SuggestionOverwriteError(RuntimeError):
    """Accepting evidence would replace a manually maintained value."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("Ein manuell gepflegter Kundenwert wird nicht überschrieben.")
        self.current = current


class SourceUnavailableError(RuntimeError):
    """The mounted source is missing, empty, or does not match its identity."""
