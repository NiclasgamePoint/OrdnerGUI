"""Compatibility import for the focused read-only catalog adapter."""

from .sqlite_catalog_reader import CatalogUnavailableError, SQLiteCatalogReader

__all__ = ["CatalogUnavailableError", "SQLiteCatalogReader"]
