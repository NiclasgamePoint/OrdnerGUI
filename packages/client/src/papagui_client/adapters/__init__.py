"""Infrastructure adapters for the PapaGUI client."""

from .filesystem_generations import FilesystemGenerationStore
from .http_api import HttpCustomerGateway, HttpGenerationGateway, HttpServerControlGateway
from .http_journal import HttpJournalGateway
from .http_review import HttpReviewGateway
from .json_config import JsonClientConfigRepository
from .json_search_history import JsonSearchHistoryRepository
from .sqlite_catalog import SQLiteCatalogReader
from .sqlite_customers import SQLiteCustomerOutbox, SQLiteCustomerSnapshot
from .sqlite_journal_outbox import SQLiteJournalOutbox

__all__ = [
    "FilesystemGenerationStore",
    "HttpCustomerGateway",
    "HttpGenerationGateway",
    "HttpJournalGateway",
    "HttpReviewGateway",
    "HttpServerControlGateway",
    "JsonClientConfigRepository",
    "JsonSearchHistoryRepository",
    "SQLiteCatalogReader",
    "SQLiteCustomerOutbox",
    "SQLiteCustomerSnapshot",
    "SQLiteJournalOutbox",
]
