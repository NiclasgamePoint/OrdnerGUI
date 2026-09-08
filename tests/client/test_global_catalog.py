from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import sqlite3

import pytest

from papagui_client.adapters.json_search_history import JsonSearchHistoryRepository
from papagui_client.adapters.sqlite_catalog import (
    CatalogUnavailableError,
    SQLiteCatalogReader,
)
from papagui_client.adapters.sqlite_catalog_documents import CatalogDocumentQueries
from papagui_client.application.catalog import CatalogSearchService
from papagui_client.application.models import (
    GlobalSearchKind,
    GlobalSearchQuery,
    GlobalSearchSort,
)
from papagui_client.application.paths import SourceMapping, SourcePathResolver


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshots(tmp_path):
    catalog = tmp_path / "catalog.db"
    connection = sqlite3.connect(catalog)
    connection.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            document_key TEXT UNIQUE NOT NULL,
            source_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            modified_date TEXT NOT NULL,
            year TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            project_name TEXT NOT NULL,
            domain_folder TEXT NOT NULL,
            time_bucket TEXT NOT NULL,
            relative_dir TEXT NOT NULL,
            folder_id INTEGER,
            project_root_id INTEGER
        );
        CREATE TABLE folders (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_id INTEGER,
            domain_folder TEXT NOT NULL,
            time_bucket TEXT NOT NULL,
            project_name TEXT NOT NULL,
            project_root_id INTEGER
        );
        CREATE TABLE project_roots (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            service_type TEXT NOT NULL,
            year INTEGER NOT NULL,
            customer_label TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            city TEXT NOT NULL,
            recognition_key TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO folders VALUES(?,?,?,?,?,?,?,?,?)",
        (
            (1, "archive", "Heizung/2026/Muster", "Muster", None, "Heizung", "2026", "Muster", 7),
            (2, "archive", "Heizung/2026/Muster/Angebote", "Angebote", 1, "Heizung", "2026", "Muster", 7),
            (3, "archive", "Elektro/2025/Andere", "Andere", None, "Elektro", "2025", "Andere", 8),
        ),
    )
    connection.executemany(
        "INSERT INTO project_roots VALUES(?,?,?,?,?,?,?,?,?)",
        (
            (7, "archive", "Heizung/2026/Muster", "Heizung", 2026, "Muster", "Muster GmbH", "Berlin", "muster"),
            (8, "archive", "Elektro/2025/Andere", "Elektro", 2025, "Andere", "Andere AG", "Hamburg", "andere"),
        ),
    )
    connection.executemany(
        "INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (1, "/source/one", "doc-1", "archive", "Heizung/2026/Muster/Angebot.pdf", "Angebot.pdf", "pdf", 120, "2026-06-02", "2026", "Muster GmbH", "Muster", "Heizung", "2026", "Heizung/2026/Muster", 1, 7),
            (2, "/source/two", "doc-2", "archive", "Elektro/2025/Andere/Rechnung.txt", "Rechnung.txt", "txt", 80, "2025-04-01", "2025", "Andere AG", "Andere", "Elektro", "2025", "Elektro/2025/Andere", 3, 8),
        ),
    )
    connection.commit()
    connection.close()

    customers = tmp_path / "customers.db"
    connection = sqlite3.connect(customers)
    connection.execute(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY,display_name TEXT,company TEXT,"
        "city TEXT,email TEXT,phone TEXT,revision INTEGER)"
    )
    connection.executemany(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        (
            (11, "Muster GmbH", "Muster Holding", "Berlin", "info@muster.test", "", 3),
            (12, "Andere AG", "", "Hamburg", "", "", 1),
        ),
    )
    connection.commit()
    connection.close()
    return catalog, customers


def _service(tmp_path):
    catalog, customers = _snapshots(tmp_path)
    history = JsonSearchHistoryRepository(tmp_path / "search-history.json", maximum=3)
    return (
        CatalogSearchService(
            SQLiteCatalogReader(catalog, customers),
            SourcePathResolver([SourceMapping("archive", linux="/mnt/archive")]),
            history,
        ),
        catalog,
        customers,
        history,
    )


def test_portable_folders_projects_facets_and_folder_details_are_read_only(tmp_path):
    service, catalog, customers, _history = _service(tmp_path)
    before = (_digest(catalog), _digest(customers))

    facets = service.facets("archive")
    assert facets.domains == ("Elektro", "Heizung")
    assert facets.years == ("2026", "2025")
    assert facets.file_types == ("pdf", "txt")

    roots = service.project_roots("archive")
    assert [item.project.customer_name for item in roots] == ["Andere AG", "Muster GmbH"]
    assert roots[1].local_path == PurePosixPath("/mnt/archive/Heizung/2026/Muster")
    folders = service.folders("archive", project_only=True)
    assert {item.folder.name for item in folders} == {"Muster", "Angebote", "Andere"}
    details = service.folder("archive", "Heizung/2026/Muster")
    assert details.folder.folder.file_count == 1
    assert details.children[0].folder.name == "Angebote"
    assert details.documents[0].record.document_key == "doc-1"
    assert before == (_digest(catalog), _digest(customers))


def test_global_search_all_entities_facets_sorting_pagination_and_history(tmp_path):
    service, _catalog, _customers, history = _service(tmp_path)
    page = service.global_search(GlobalSearchQuery(text="Muster", limit=2))
    assert page.total == 5
    assert len(page.items) == 2
    assert page.has_next and not page.has_previous
    assert page.kind_counts == {
        "document": 1,
        "folder": 2,
        "project": 1,
        "customer": 1,
    }
    assert page.items[0].local_path is not None
    second = service.global_search(
        GlobalSearchQuery(text="Muster", limit=2, offset=2)
    )
    assert second.has_previous and second.has_next
    assert service.history() == ("Muster",)

    documents = service.global_search(
        GlobalSearchQuery(
            file_type=".pdf",
            domain_folder="Heizung",
            year="2026",
            kinds=(GlobalSearchKind.DOCUMENT,),
            sort=GlobalSearchSort.MODIFIED,
        )
    )
    assert documents.total == 1
    assert documents.items[0].record.key == "doc-1"
    named = service.global_search(
        GlobalSearchQuery(kinds=(GlobalSearchKind.CUSTOMER,), sort=GlobalSearchSort.NAME)
    )
    assert [item.record.title for item in named.items] == ["Andere AG", "Muster GmbH"]

    for term in ("Andere", "Dritte", "Vierte", "andere"):
        history.add(term)
    assert history.entries() == ("andere", "Vierte", "Dritte")
    service.clear_history()
    assert service.history() == ()
    payload = json.loads(history.path.read_text(encoding="utf-8"))
    assert payload == {"schema_version": 1, "entries": []}


def test_global_query_and_history_edge_validation(tmp_path):
    with pytest.raises(ValueError):
        GlobalSearchQuery(limit=0)
    with pytest.raises(ValueError):
        GlobalSearchQuery(offset=-1)
    with pytest.raises(ValueError):
        GlobalSearchQuery(kinds=("unknown",))
    with pytest.raises(ValueError):
        JsonSearchHistoryRepository(tmp_path / "x", maximum=0)

    history = JsonSearchHistoryRepository(tmp_path / "history.json")
    history.path.write_text("broken", encoding="utf-8")
    assert history.entries() == ()
    history.path.write_text('{"entries":"wrong"}', encoding="utf-8")
    assert history.entries() == ()
    assert history.add("  ") == ()

    database = tmp_path / "bad.db"
    sqlite3.connect(database).execute("CREATE TABLE files(name TEXT)").connection.close()
    reader = SQLiteCatalogReader(database)
    with pytest.raises(CatalogUnavailableError, match="portable"):
        reader.facets()


def test_full_text_search_is_not_correlated_per_catalog_row(tmp_path):
    """Guard against the GUI-freezing correlated FTS query from the crash dump."""
    database = tmp_path / "catalog.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE files (
            path TEXT,
            document_key TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            filename TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE file_content_fts USING fts5(path UNINDEXED, content);
        INSERT INTO files VALUES('/source/one', 'one', 'archive', 'one.txt', 'one.txt');
        INSERT INTO file_content_fts VALUES('/source/one', 'gesuchter Inhalt');
        """
    )
    clauses: list[str] = []
    values: list[object] = []
    CatalogDocumentQueries._append_text(
        clauses,
        values,
        "gesuchter",
        {"path", "filename"},
        {"files", "file_content_fts"},
    )
    plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT document_key FROM files WHERE " + clauses[0],
        values,
    ).fetchall()
    rows = connection.execute(
        "SELECT document_key FROM files WHERE " + clauses[0], values
    ).fetchall()
    connection.close()

    assert rows == [("one",)]
    assert not any("CORRELATED" in str(row[3]).upper() for row in plan)
