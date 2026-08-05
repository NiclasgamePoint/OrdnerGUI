from __future__ import annotations

from unittest.mock import patch

from app.core.content_index import ContentSearchHit, ContentStateRepository
from app.core.content_search import ContentSearchService
from app.core.index_layout import IndexLayout
from app.core.search_models import SearchFilters, SearchSort
from tests.base.test_case import PapaGuiTestCase


class ContentSearchEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.layout = IndexLayout(self.temp_path / "index")
        self.layout.ensure_directories()
        self.service = ContentSearchService(self.layout, self.layout.catalog_path)

    def test_missing_corrupt_state_and_unavailable_shard(self):
        page = self.service.search_page("query", SearchFilters())
        self.assertEqual(page.total, 0)
        self.layout.catalog_path.touch()
        self.layout.content_state_path.write_bytes(b"invalid")
        page = self.service.search_page("query", SearchFilters())
        self.assertEqual(page.coverage.unavailable_shards, 1)

        self.layout.content_state_path.unlink()
        with ContentStateRepository(self.layout.content_state_path) as state:
            state.connection.execute(
                "INSERT INTO shards(name,partition_year,sequence,status,updated_at) "
                "VALUES('broken.db',2026,1,'open','now')"
            )
            state.connection.commit()
        self.layout.shard_path("broken.db").write_bytes(b"invalid")
        page = self.service.search_page("query", SearchFilters())
        self.assertEqual(page.coverage.unavailable_shards, 1)
        self.assertFalse(page.coverage.complete)

    def test_metadata_filters_result_and_sort_branches(self):
        self.assertEqual(self.service._catalog_metadata(set()), {})
        row = {
            "domain_folder": "Domain", "year": 2026, "file_type": "txt",
            "path": "/a.txt", "filename": "A.txt", "project_root_path": "",
            "folder_path": "/", "modified_date": "2026-01-01",
        }
        self.assertFalse(self.service._matches_filters(
            row, SearchFilters(domain_folder="Other")
        ))
        self.assertFalse(self.service._matches_filters(row, SearchFilters(year="2025")))
        self.assertFalse(self.service._matches_filters(row, SearchFilters(file_type="pdf")))
        self.assertTrue(self.service._matches_filters(row, SearchFilters()))
        hit = ContentSearchHit("key", "v1", "/a.txt", "excerpt", 1.0, "one.db")
        result = self.service._result(hit, row)
        self.assertEqual(result["folder_path"], "/")

    def test_date_sort_with_controlled_search_results(self):
        self.layout.content_state_path.touch()
        self.layout.catalog_path.touch()
        progress = type("Progress", (), {
            "completed_documents": 2, "total_documents": 2,
            "completed_bytes": 2, "total_bytes": 2, "complete": True,
        })()
        state_patch = patch("app.core.content_search.ContentStateRepository")
        state = state_patch.start()
        self.addCleanup(state_patch.stop)
        state.return_value.__enter__.return_value.progress.return_value = progress
        with (
            patch("app.core.content_search.ShardRepository") as shards,
            patch.object(self.service, "_catalog_metadata") as metadata,
            patch("app.core.content_search.ContentShard") as content_shard,
        ):
            shards.return_value.shard_names.return_value = ["one.db"]
            content_shard.return_value.__enter__.return_value.search.return_value = [
                ContentSearchHit("a", "v1", "/a", "A", 1.0, "one.db"),
                ContentSearchHit("b", "v1", "/b", "B", 1.0, "one.db"),
            ]
            metadata.return_value = {
                "a": {"source_version": "v1", "domain_folder": "", "year": 2025,
                      "file_type": "txt", "path": "/a", "filename": "A", "project_root_path": "",
                      "folder_path": "/", "modified_date": "2025"},
                "b": {"source_version": "v1", "domain_folder": "", "year": 2026,
                      "file_type": "txt", "path": "/b", "filename": "B", "project_root_path": "",
                      "folder_path": "/", "modified_date": "2026"},
            }
            page = self.service.search_page(
                "query", SearchFilters(sort_order=SearchSort.DATE), page_size=1
            )
            filtered = self.service.search_page(
                "query", SearchFilters(file_type="pdf"), page_size=1
            )
        self.assertEqual(page.items[0]["filename"], "B")
        self.assertEqual(filtered.total, 0)
