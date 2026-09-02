"""Stable imports for the focused SQLite customer adapters."""

from papagui_server.adapters.customer_repository import SqliteCustomerRepository
from papagui_server.adapters.customer_schema import (
    initialize_customer_schema as _initialize_schema,
)
from papagui_server.adapters.customer_uow import (
    SqliteCustomerUnitOfWork,
    SqliteCustomerUnitOfWorkFactory,
)

__all__ = [
    "SqliteCustomerRepository",
    "SqliteCustomerUnitOfWork",
    "SqliteCustomerUnitOfWorkFactory",
    "_initialize_schema",
]
