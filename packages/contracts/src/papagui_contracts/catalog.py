"""Portable catalog DTOs used by server APIs and downloaded snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ._base import (
    ContractValidationError,
    JsonDto,
    mapping_get,
    optional_int,
    require_int,
    require_mapping,
    require_string,
    string_tuple,
)
from .generations import SourcePath


@dataclass(frozen=True, slots=True)
class CatalogFile(JsonDto):
    id: int
    source: SourcePath
    filename: str
    file_type: str = ""
    file_size: int = 0
    modified_at: str = ""
    domain_folder: str = ""
    time_bucket: str = ""
    project_name: str = ""
    relative_dir: str = ""
    folder_id: int | None = None
    project_root_id: int | None = None

    def __post_init__(self) -> None:
        require_int(self.id, "id", minimum=1)
        if not isinstance(self.source, SourcePath):
            raise ContractValidationError("source must be a SourcePath DTO")
        require_string(self.filename, "filename")
        require_int(self.file_size, "file_size", minimum=0)
        for name in (
            "file_type", "modified_at", "domain_folder", "time_bucket",
            "project_name", "relative_dir",
        ):
            require_string(getattr(self, name), name, allow_empty=True)
        optional_int(self.folder_id, "folder_id", minimum=1)
        optional_int(self.project_root_id, "project_root_id", minimum=1)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CatalogFile":
        payload = require_mapping(payload, "catalog_file")
        raw_source = mapping_get(payload, "source", payload)
        return cls(
            id=require_int(mapping_get(payload, "id", 0), "id", minimum=1),
            source=SourcePath.from_dict(require_mapping(raw_source, "source")),
            filename=require_string(mapping_get(payload, "filename", ""), "filename"),
            file_type=require_string(mapping_get(payload, "file_type", ""), "file_type", allow_empty=True),
            file_size=require_int(mapping_get(payload, "file_size", 0), "file_size", minimum=0),
            modified_at=require_string(mapping_get(payload, "modified_at", mapping_get(payload, "modified_date", "")), "modified_at", allow_empty=True),
            domain_folder=require_string(mapping_get(payload, "domain_folder", ""), "domain_folder", allow_empty=True),
            time_bucket=require_string(mapping_get(payload, "time_bucket", ""), "time_bucket", allow_empty=True),
            project_name=require_string(mapping_get(payload, "project_name", ""), "project_name", allow_empty=True),
            relative_dir=require_string(mapping_get(payload, "relative_dir", ""), "relative_dir", allow_empty=True),
            folder_id=optional_int(mapping_get(payload, "folder_id", None), "folder_id", minimum=1),
            project_root_id=optional_int(mapping_get(payload, "project_root_id", None), "project_root_id", minimum=1),
        )


@dataclass(frozen=True, slots=True)
class CatalogFolder(JsonDto):
    id: int
    source: SourcePath
    name: str
    parent_id: int | None = None
    project_root_id: int | None = None
    file_count: int = 0
    total_size: int = 0
    last_modified: str | None = None

    def __post_init__(self) -> None:
        require_int(self.id, "id", minimum=1)
        if not isinstance(self.source, SourcePath):
            raise ContractValidationError("source must be a SourcePath DTO")
        require_string(self.name, "name")
        optional_int(self.parent_id, "parent_id", minimum=1)
        optional_int(self.project_root_id, "project_root_id", minimum=1)
        require_int(self.file_count, "file_count", minimum=0)
        require_int(self.total_size, "total_size", minimum=0)
        if self.last_modified is not None:
            require_string(self.last_modified, "last_modified")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CatalogFolder":
        payload = require_mapping(payload, "catalog_folder")
        return cls(
            id=require_int(mapping_get(payload, "id", 0), "id", minimum=1),
            source=SourcePath.from_dict(require_mapping(mapping_get(payload, "source", payload), "source")),
            name=require_string(mapping_get(payload, "name", ""), "name"),
            parent_id=optional_int(mapping_get(payload, "parent_id", None), "parent_id", minimum=1),
            project_root_id=optional_int(mapping_get(payload, "project_root_id", None), "project_root_id", minimum=1),
            file_count=require_int(mapping_get(payload, "file_count", 0), "file_count", minimum=0),
            total_size=require_int(mapping_get(payload, "total_size", 0), "total_size", minimum=0),
            last_modified=(
                None if mapping_get(payload, "last_modified", None) is None
                else require_string(mapping_get(payload, "last_modified", ""), "last_modified")
            ),
        )


@dataclass(frozen=True, slots=True)
class CatalogProjectRoot(JsonDto):
    id: int
    source: SourcePath
    service_type: str
    year: int
    customer_label: str
    customer_name: str
    city: str = ""
    recognition_key: str = ""

    def __post_init__(self) -> None:
        require_int(self.id, "id", minimum=1)
        if not isinstance(self.source, SourcePath):
            raise ContractValidationError("source must be a SourcePath DTO")
        require_int(self.year, "year", minimum=1900)
        for name in ("service_type", "customer_label", "customer_name"):
            require_string(getattr(self, name), name)
        require_string(self.city, "city", allow_empty=True)
        require_string(self.recognition_key, "recognition_key", allow_empty=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CatalogProjectRoot":
        payload = require_mapping(payload, "catalog_project_root")
        return cls(
            id=require_int(mapping_get(payload, "id", 0), "id", minimum=1),
            source=SourcePath.from_dict(require_mapping(mapping_get(payload, "source", payload), "source")),
            service_type=require_string(mapping_get(payload, "service_type", ""), "service_type"),
            year=require_int(mapping_get(payload, "year", 0), "year", minimum=1900),
            customer_label=require_string(mapping_get(payload, "customer_label", ""), "customer_label"),
            customer_name=require_string(mapping_get(payload, "customer_name", ""), "customer_name"),
            city=require_string(mapping_get(payload, "city", ""), "city", allow_empty=True),
            recognition_key=require_string(mapping_get(payload, "recognition_key", ""), "recognition_key", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class CatalogFacets(JsonDto):
    domains: tuple[str, ...] = ()
    years: tuple[str, ...] = ()
    file_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("domains", "years", "file_types"):
            object.__setattr__(self, name, string_tuple(getattr(self, name), name))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CatalogFacets":
        payload = require_mapping(payload, "catalog_facets")
        return cls(
            domains=string_tuple(mapping_get(payload, "domains", ()), "domains"),
            years=string_tuple(mapping_get(payload, "years", ()), "years"),
            file_types=string_tuple(mapping_get(payload, "file_types", ()), "file_types"),
        )
