from __future__ import annotations

from pathlib import Path

import pytest

from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.adapters.catalog_reader import SqliteCatalogReader
from papagui_server.domain.errors import ResourceNotFoundError
from papagui_server.domain.models import ServerSettings


def _reader(tmp_path: Path) -> SqliteCatalogReader:
    source = tmp_path / "source"
    first = source / "Planung" / "2025" / "Acme GmbH, Köln"
    (first / "Unterlagen").mkdir(parents=True)
    (first / "Angebot.txt").write_text("Geheimertext Alpha", encoding="utf-8")
    (first / "Unterlagen" / "Plan.md").write_text("Geheimertext Beta", encoding="utf-8")
    second = source / "Beratung" / "2026" / "Beta AG, Bonn"
    second.mkdir(parents=True)
    (second / "Zeta.csv").write_text("nummer,wert\n1,Omega", encoding="utf-8")
    generic = source / "Ablage"
    generic.mkdir()
    (generic / "Readme.md").write_text("Allgemein", encoding="utf-8")
    database = SqliteCatalogIndexer(tmp_path / "data").build(
        source,
        source_id="primary",
        full_rebuild=True,
        settings=ServerSettings(),
        cancelled=lambda: False,
        progress=lambda *_args: None,
    )
    return SqliteCatalogReader(database)


def test_reader_exposes_portable_search_facets_and_folder_tree(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    result = reader.search(source_id="primary", query="Geheimertext", sort="name")
    assert result["total"] == 2
    assert [item["filename"] for item in result["items"]] == ["Angebot.txt", "Plan.md"]
    assert all(item["source"]["source_id"] == "primary" for item in result["items"])
    assert all(not item["source"]["relative_path"].startswith("/") for item in result["items"])
    assert reader.search(source_id="other", query="Geheimertext")["total"] == 0

    planning = reader.search(
        source_id="primary",
        domain_folder="Planung",
        time_bucket="2025",
        file_type=".txt",
        sort="modified",
    )
    assert planning["total"] == 1
    root_id = planning["items"][0]["project_root_id"]
    assert (
        reader.search(source_id="primary", project_root_id=root_id, sort="size", limit=1, offset=1)[
            "items"
        ][0]["filename"]
        == "Plan.md"
    )

    facets = reader.facets(source_id="primary")
    assert facets["domains"] == ["Ablage", "Beratung", "Planung"]
    assert facets["years"] == ["2026", "2025"]
    assert set(facets["file_types"]) == {"csv", "md", "txt"}

    folders = reader.list_folders(source_id="primary", query="Acme")
    assert [item["name"] for item in folders] == ["Acme GmbH, Köln", "Unterlagen"]
    projects = reader.list_folders(source_id="primary", project_only=True)
    assert {item["name"] for item in projects} == {"Acme GmbH, Köln", "Beta AG, Bonn"}
    details = reader.folder_details(
        source_id="primary", relative_path="Planung/2025/Acme GmbH, Köln"
    )
    assert details["folder"]["file_count"] == 2
    assert details["children"][0]["name"] == "Unterlagen"


def test_reader_projects_document_bounds_and_validation(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    roots = reader.list_project_roots(source_id="primary")
    assert [(item["customer_name"], item["year"]) for item in roots] == [
        ("Acme GmbH", 2025),
        ("Beta AG", 2026),
    ]
    root = reader.project_root(source_id="primary", project_root_id=roots[0]["id"])
    assert root["source"]["relative_path"] == "Planung/2025/Acme GmbH, Köln"
    evidence = reader.document_evidence(source_id="primary", documents_per_project=1)
    assert len(evidence) == 2
    assert len({item["project_root_id"] for item in evidence}) == 2

    with pytest.raises(ValueError):
        reader.search(source_id="bad/id")
    with pytest.raises(ValueError):
        reader.search(source_id="primary", sort="unknown")
    with pytest.raises(ValueError):
        reader.search(source_id="primary", limit=True)
    with pytest.raises(ValueError):
        reader.search(source_id="primary", offset=-1)
    with pytest.raises(ValueError):
        reader.search(source_id="primary", project_root_id=True)
    with pytest.raises(ValueError):
        reader.project_root(source_id="primary", project_root_id=0)
    with pytest.raises(ValueError):
        reader.document_evidence(source_id="primary", documents_per_project=0)
    with pytest.raises(ResourceNotFoundError):
        reader.project_root(source_id="primary", project_root_id=999)
    with pytest.raises(ResourceNotFoundError):
        reader.folder_details(source_id="primary", relative_path="missing")
    with pytest.raises(ResourceNotFoundError):
        SqliteCatalogReader(tmp_path / "missing.db").facets(source_id="primary")
