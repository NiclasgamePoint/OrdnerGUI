"""Bound SQL work for recognition without relying on wall-clock timing."""

from contextlib import contextmanager
import sqlite3

import pytest

from papagui_server.adapters.catalog_reader import SqliteCatalogReader
from papagui_server.adapters.catalog_storage import CatalogSqliteWriter


@pytest.mark.parametrize("legacy", [False, True])
def test_rank_once_and_fetch_correct_text_with_unrelated_fts_rowids(tmp_path, legacy):
    path = tmp_path / "catalog.db"
    connection = sqlite3.connect(path)
    try:
        CatalogSqliteWriter().initialize(connection)
        connection.executemany(
            """INSERT INTO files(path,source_id,relative_path,filename,file_size,file_type,
                modified_date,modified_ns,document_key,project_root_id)
                VALUES (?,?,?,?,0,'txt','2026-01-01',0,?,?)""",
            [(f"source://primary/{n:04}.txt", "primary", f"{n:04}.txt", f"{n:04}.txt",
              str(n), n // 30 + 1) for n in range(1500)],
        )
        connection.executemany(
            "INSERT INTO file_content_fts(rowid,path,content) VALUES (?,?,?)",
            [(10000 + 3 * n, f"source://primary/{n:04}.txt", f"Synthetic document {n:04}")
             for n in range(1500) if n != 10],
        )
        # Neither an orphaned FTS row nor metadata without text is evidence.
        connection.execute("INSERT INTO file_content_fts(path,content) VALUES ('orphan','Orphan')")
        if legacy:
            connection.execute("ALTER TABLE files DROP COLUMN extraction_status")
        connection.commit()
    finally:
        connection.close()

    class BoundedReader(SqliteCatalogReader):
        @contextmanager
        def _connection(self):
            with super()._connection() as database:
                steps = 0

                def budget():
                    nonlocal steps
                    steps += 1000
                    return int(steps > 200_000)

                database.set_progress_handler(budget, 1000)
                yield database

    reader = BoundedReader(path)
    for offset, expected in [(0, [0, 1, 2, 3]), (8, [8, 9, 11, 12])]:
        rows = reader.document_evidence(
            source_id="primary", project_root_ids=[1],
            documents_per_project=4, offset_per_project=offset,
        )
        assert [row["content"] for row in rows] == [f"Synthetic document {n:04}" for n in expected]
        assert [row["source"]["relative_path"] for row in rows] == [f"{n:04}.txt" for n in expected]
        assert {row["project_root_id"] for row in rows} == {1}
