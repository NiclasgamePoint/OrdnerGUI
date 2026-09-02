"""SQLite row mappings for portable catalog contracts."""

from __future__ import annotations

import sqlite3

from papagui_contracts import CatalogFolder, CatalogProjectRoot, SourcePath

from papagui_client.application.models import CatalogRecord


def record(row: sqlite3.Row) -> CatalogRecord:
    values = dict(row)
    known = {
        "document_key",
        "source_id",
        "relative_path",
        "filename",
        "file_type",
        "customer_name",
        "project_name",
        "year",
        "modified_date",
        "file_size",
        "domain_folder",
        "time_bucket",
        "relative_dir",
        "folder_id",
        "project_root_id",
    }
    return CatalogRecord(
        document_key=str(values["document_key"]),
        source_id=str(values["source_id"]),
        relative_path=str(values["relative_path"]),
        filename=str(values["filename"]),
        file_type=str(values.get("file_type") or ""),
        customer_name=str(values.get("customer_name") or ""),
        project_name=str(values.get("project_name") or ""),
        year=str(values.get("year") or values.get("time_bucket") or ""),
        modified_date=str(values.get("modified_date") or ""),
        file_size=int(values.get("file_size") or 0),
        domain_folder=str(values.get("domain_folder") or ""),
        time_bucket=str(values.get("time_bucket") or ""),
        relative_dir=str(values.get("relative_dir") or ""),
        folder_id=(int(values["folder_id"]) if values.get("folder_id") is not None else None),
        project_root_id=(
            int(values["project_root_id"])
            if values.get("project_root_id") is not None
            else None
        ),
        metadata={key: value for key, value in values.items() if key not in known},
    )


def folder(row: sqlite3.Row) -> CatalogFolder:
    return CatalogFolder(
        id=int(row["id"]),
        source=SourcePath(str(row["source_id"]), str(row["relative_path"])),
        name=str(row["name"]),
        parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
        project_root_id=(
            int(row["project_root_id"])
            if row["project_root_id"] is not None
            else None
        ),
        file_count=int(row["file_count"]),
        total_size=int(row["total_size"]),
        last_modified=(
            str(row["last_modified"]) if row["last_modified"] is not None else None
        ),
    )


def project(row: sqlite3.Row) -> CatalogProjectRoot:
    return CatalogProjectRoot(
        id=int(row["id"]),
        source=SourcePath(str(row["source_id"]), str(row["relative_path"])),
        service_type=str(row["service_type"]),
        year=int(row["year"]),
        customer_label=str(row["customer_label"]),
        customer_name=str(row["customer_name"]),
        city=str(row["city"]),
        recognition_key=str(row["recognition_key"]),
    )
