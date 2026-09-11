from __future__ import annotations

import hashlib
from pathlib import PurePosixPath, PureWindowsPath
import sqlite3

import pytest

from papagui_contracts import SourcePath
from papagui_client.adapters.sqlite_catalog import SQLiteCatalogReader
from papagui_client.application.catalog import CatalogSearchService
from papagui_client.application.models import CatalogQuery
from papagui_client.application.paths import (
    PlatformFamily,
    SourceMapping,
    SourcePathResolver,
    UnknownSourceError,
    UnsafeRelativePathError,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_path_mapping_is_platform_specific():
    resolver = SourcePathResolver(
        [
            SourceMapping(
                "archive",
                windows=r"Z:\Kundendaten",
                macos="/Volumes/Kundendaten",
                linux="/mnt/kundendaten",
            )
        ]
    )
    source = SourcePath("archive", "2026/Muster/angebot.pdf")

    assert resolver.resolve(source, platform=PlatformFamily.WINDOWS) == PureWindowsPath(
        r"Z:\Kundendaten\2026\Muster\angebot.pdf"
    )
    assert resolver.resolve(source, platform=PlatformFamily.MACOS) == PurePosixPath(
        "/Volumes/Kundendaten/2026/Muster/angebot.pdf"
    )
    assert resolver.resolve(source, platform=PlatformFamily.LINUX) == PurePosixPath(
        "/mnt/kundendaten/2026/Muster/angebot.pdf"
    )

    unc = SourcePathResolver(
        [SourceMapping("archive", windows=r"\\nas\Kundendaten")]
    ).resolve(source, platform=PlatformFamily.WINDOWS)
    assert unc == PureWindowsPath(r"\\nas\Kundendaten\2026\Muster\angebot.pdf")


def test_source_path_resolver_rejects_missing_mapping_and_traversal():
    resolver = SourcePathResolver([SourceMapping("archive", linux="/mnt/archive")])
    with pytest.raises(UnknownSourceError):
        resolver.resolve("missing", "file.pdf", platform="linux")
    with pytest.raises(UnsafeRelativePathError):
        resolver.resolve("archive", "../secret", platform="linux")
    with pytest.raises(UnsafeRelativePathError):
        resolver.resolve("archive", r"C:\secret", platform="linux")


def test_catalog_reader_searches_read_only_portable_records(tmp_path):
    database = tmp_path / "catalog.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE files (
            document_key TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_type TEXT,
            customer_name TEXT,
            project_name TEXT,
            year TEXT,
            modified_date TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)",
        [
            ("one", "archive", "2026/Muster/Angebot.pdf", "Angebot.pdf", "pdf", "Muster", "A", "2026", "2026-02-01"),
            ("two", "archive", "2025/Andere/Rechnung.pdf", "Rechnung.pdf", "pdf", "Andere", "B", "2025", "2025-02-01"),
        ],
    )
    connection.commit()
    connection.close()
    before = digest(database)

    reader = SQLiteCatalogReader(database)
    service = CatalogSearchService(
        reader, SourcePathResolver([SourceMapping("archive", **dict.fromkeys(("windows", "macos", "linux"), str(tmp_path / "archive")))])
    )
    results = service.search(CatalogQuery(text="Muster", year="2026"))

    assert [result.record.document_key for result in results] == ["one"]
    assert results[0].local_path == tmp_path / "archive/2026/Muster/Angebot.pdf"
    assert reader.get("two").filename == "Rechnung.pdf"
    assert digest(database) == before
