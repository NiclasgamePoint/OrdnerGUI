"""Facade composing focused read-only SQLite catalog queries."""

from __future__ import annotations

from papagui_contracts import CatalogFacets, CatalogFolder, CatalogProjectRoot

from papagui_client.application.models import (
    CatalogQuery,
    CatalogRecord,
    GlobalSearchHit,
    GlobalSearchKind,
    GlobalSearchPage,
    GlobalSearchQuery,
)

from .sqlite_catalog_documents import CatalogDocumentQueries
from .sqlite_catalog_global import customer_records, sort_records
from .sqlite_catalog_navigation import CatalogNavigationQueries
from .sqlite_catalog_schema import (
    CatalogUnavailableError,
    DatabasePath,
    provider,
)


class SQLiteCatalogReader:
    """Expose no writer capability while joining index and customer snapshots."""

    def __init__(
        self,
        database: DatabasePath,
        customer_database: DatabasePath | None = None,
    ) -> None:
        catalog = provider(database)
        self._documents = CatalogDocumentQueries(catalog)
        self._navigation = CatalogNavigationQueries(catalog)
        self._customer_database = (
            provider(customer_database) if customer_database is not None else None
        )

    def search(self, query: CatalogQuery) -> list[CatalogRecord]:
        return self._documents.search(query)

    def get(self, document_key: str) -> CatalogRecord | None:
        return self._documents.get(document_key)

    def facets(self, source_id: str | None = None) -> CatalogFacets:
        return self._documents.facets(source_id)

    def folders(
        self,
        source_id: str | None = None,
        *,
        query: str = "",
        project_only: bool = False,
    ) -> list[CatalogFolder]:
        return self._navigation.folders(
            source_id, query=query, project_only=project_only
        )

    def folder(
        self, source_id: str, relative_path: str
    ) -> tuple[CatalogFolder, list[CatalogFolder], list[CatalogRecord]]:
        return self._navigation.folder(source_id, relative_path)

    def project_roots(self, source_id: str | None = None) -> list[CatalogProjectRoot]:
        return self._navigation.project_roots(source_id)

    def global_search(self, query: GlobalSearchQuery) -> GlobalSearchPage:
        selected = set(query.selected_kinds)
        records = []
        if GlobalSearchKind.DOCUMENT in selected:
            records.extend(self._documents.global_records(query))
        if GlobalSearchKind.FOLDER in selected and query.file_type is None:
            records.extend(self._navigation.folder_records(query))
        if GlobalSearchKind.PROJECT in selected and query.file_type is None:
            records.extend(self._navigation.project_records(query))
        if (
            GlobalSearchKind.CUSTOMER in selected
            and query.source_id is None
            and query.domain_folder is None
            and query.year is None
            and query.file_type is None
        ):
            records.extend(customer_records(self._customer_database, query))
        counts = {
            kind.value: sum(record.kind is kind for record in records)
            for kind in GlobalSearchKind
        }
        sort_records(records, query)
        total = len(records)
        page = records[query.offset : query.offset + query.limit]
        return GlobalSearchPage(
            items=tuple(GlobalSearchHit(item) for item in page),
            total=total,
            limit=query.limit,
            offset=query.offset,
            facets=self.facets(query.source_id),
            kind_counts=counts,
        )


__all__ = ["CatalogUnavailableError", "SQLiteCatalogReader"]
