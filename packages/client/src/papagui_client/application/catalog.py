"""Read-only catalog search use cases."""

from __future__ import annotations

from dataclasses import replace

from papagui_contracts.generations import SourcePath

from .models import (
    CatalogFolderDetails,
    CatalogFolderHit,
    CatalogHit,
    CatalogProjectRootHit,
    CatalogQuery,
    GlobalSearchHit,
    GlobalSearchPage,
    GlobalSearchQuery,
)
from .paths import SourcePathResolver, UnknownSourceError
from .ports import CatalogReader, SearchHistoryStore


class CatalogSearchService:
    def __init__(
        self,
        reader: CatalogReader,
        paths: SourcePathResolver,
        history: SearchHistoryStore | None = None,
    ) -> None:
        self._reader = reader
        self._paths = paths
        self._history = history

    def search(self, query: CatalogQuery | str = "", **filters: object) -> list[CatalogHit]:
        if isinstance(query, str):
            query = CatalogQuery(text=query, **filters)
        return [self._resolve(record) for record in self._reader.search(query)]

    def get(self, document_key: str) -> CatalogHit | None:
        record = self._reader.get(document_key)
        return self._resolve(record) if record is not None else None

    def global_search(self, query: GlobalSearchQuery) -> GlobalSearchPage:
        if self._history is not None and query.text.strip():
            self._history.add(query.text)
        page = self._reader.global_search(query)
        return replace(
            page,
            items=tuple(self._resolve_global(item) for item in page.items),
        )

    def facets(self, source_id: str | None = None):
        return self._reader.facets(source_id)

    def folders(
        self,
        source_id: str | None = None,
        *,
        query: str = "",
        project_only: bool = False,
    ) -> tuple[CatalogFolderHit, ...]:
        return tuple(
            CatalogFolderHit(folder, self._paths.resolve(folder.source))
            for folder in self._reader.folders(
                source_id, query=query, project_only=project_only
            )
        )

    def folder(self, source_id: str, relative_path: str) -> CatalogFolderDetails:
        folder, children, documents = self._reader.folder(source_id, relative_path)
        return CatalogFolderDetails(
            CatalogFolderHit(folder, self._paths.resolve(folder.source)),
            tuple(
                CatalogFolderHit(item, self._paths.resolve(item.source))
                for item in children
            ),
            tuple(self._resolve(item) for item in documents),
        )

    def project_roots(
        self, source_id: str | None = None
    ) -> tuple[CatalogProjectRootHit, ...]:
        return tuple(
            CatalogProjectRootHit(project, self._paths.resolve(project.source))
            for project in self._reader.project_roots(source_id)
        )

    def history(self) -> tuple[str, ...]:
        return self._history.entries() if self._history is not None else ()

    def clear_history(self) -> None:
        if self._history is not None:
            self._history.clear()

    def _resolve(self, record):
        source = SourcePath(source_id=record.source_id, relative_path=record.relative_path)
        return CatalogHit(record=record, local_path=self._paths.resolve(source))

    def _resolve_global(self, hit: GlobalSearchHit) -> GlobalSearchHit:
        record = hit.record
        if not record.source_id or not record.relative_path:
            return hit
        try:
            local_path = self._paths.resolve(
                SourcePath(record.source_id, record.relative_path)
            )
        except UnknownSourceError:
            local_path = None
        return GlobalSearchHit(record, local_path)
