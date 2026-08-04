from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sqlite3

from app.core.content_index import ContentSearchHit, ContentShard, ContentStateRepository, ShardRepository
from app.core.index_layout import IndexLayout
from app.core.search_models import SearchCoverage, SearchFilters, SearchPage, SearchSort


class ContentSearchService:
    """Fan out a query over immutable-size shards and merge their local ranks."""

    def __init__(
        self,
        layout: IndexLayout,
        catalog_path: Path,
        maximum_parallel_shards: int = 4,
    ):
        self.layout = layout
        self.catalog_path = catalog_path
        self.maximum_parallel_shards = max(1, int(maximum_parallel_shards))

    def search_page(
        self,
        query: str,
        filters: SearchFilters,
        page: int = 1,
        page_size: int = 25,
        maximum: int = 200,
    ) -> SearchPage:
        if not self.layout.content_state_path.exists() or not self.catalog_path.exists():
            return SearchPage([], 0, page, page_size, SearchCoverage(0, 0, 0, 0, False))
        try:
            with ContentStateRepository(self.layout.content_state_path) as state:
                progress = state.progress()
                repository = ShardRepository(self.layout, state)
                years = (
                    {int(filters.year)}
                    if filters.year and filters.year.isdigit()
                    else None
                )
                shard_names = repository.shard_names(years)
        except sqlite3.DatabaseError:
            return SearchPage(
                [], 0, page, page_size, SearchCoverage(0, 0, 0, 0, False, 1)
            )
        candidate_limit = max(page * page_size, maximum)
        ranked: list[tuple[ContentSearchHit, int]] = []
        unavailable = 0

        def query_shard(name: str) -> list[ContentSearchHit]:
            with ContentShard(self.layout.shard_path(name)) as shard:
                return shard.search(query, candidate_limit)

        with ThreadPoolExecutor(max_workers=self.maximum_parallel_shards) as executor:
            futures = {executor.submit(query_shard, name): name for name in shard_names}
            for future in as_completed(futures):
                try:
                    ranked.extend(
                        (hit, position)
                        for position, hit in enumerate(future.result(), start=1)
                    )
                except (OSError, sqlite3.Error, ValueError):
                    unavailable += 1

        metadata = self._catalog_metadata({hit.document_key for hit, _ in ranked})
        scores: dict[str, float] = {}
        hits: dict[str, ContentSearchHit] = {}
        for hit, local_position in ranked:
            row = metadata.get(hit.document_key)
            if row is None or str(row["source_version"]) != hit.source_version:
                continue
            if not self._matches_filters(row, filters):
                continue
            scores[hit.document_key] = scores.get(hit.document_key, 0.0) + (
                1.0 / (60 + local_position)
            )
            hits.setdefault(hit.document_key, hit)

        items = [self._result(hits[key], metadata[key]) for key in hits]
        if filters.sort_order == SearchSort.DATE:
            items.sort(
                key=lambda item: (item["modified_date"], item["filename"].casefold()),
                reverse=True,
            )
        elif filters.sort_order == SearchSort.ALPHABETICAL:
            items.sort(key=lambda item: (item["filename"].casefold(), item["path"].casefold()))
        else:
            items.sort(key=lambda item: (-scores[item["document_key"]], item["filename"].casefold()))
        items = items[:maximum]
        offset = max(0, page - 1) * page_size
        coverage = SearchCoverage(
            completed_documents=progress.completed_documents,
            total_documents=progress.total_documents,
            completed_bytes=progress.completed_bytes,
            total_bytes=progress.total_bytes,
            complete=progress.complete and unavailable == 0,
            unavailable_shards=unavailable,
        )
        return SearchPage(
            items[offset:offset + page_size], len(items), page, page_size, coverage
        )

    def _catalog_metadata(self, keys: set[str]) -> dict[str, sqlite3.Row]:
        if not keys:
            return {}
        connection = sqlite3.connect(f"file:{self.catalog_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            result = {}
            values = sorted(keys)
            for start in range(0, len(values), 500):
                chunk = values[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT * FROM files WHERE document_key IN ({placeholders})",
                    chunk,
                )
                result.update({str(row["document_key"]): row for row in rows})
            return result
        finally:
            connection.close()

    @staticmethod
    def _matches_filters(row: sqlite3.Row, filters: SearchFilters) -> bool:
        return (
            (not filters.domain_folder or row["domain_folder"] == filters.domain_folder)
            and (not filters.year or str(row["year"] or "") == filters.year)
            and (not filters.file_type or row["file_type"] == filters.file_type)
        )

    @staticmethod
    def _result(hit: ContentSearchHit, row: sqlite3.Row) -> dict:
        return {
            "document_key": hit.document_key,
            "path": str(row["path"]),
            "filename": str(row["filename"]),
            "folder_path": str(row["project_root_path"] or row["folder_path"] or ""),
            "project_root_path": str(row["project_root_path"] or ""),
            "modified_date": str(row["modified_date"] or ""),
            "line": None,
            "excerpt": hit.excerpt,
            "source": "document",
        }
