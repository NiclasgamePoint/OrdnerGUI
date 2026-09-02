"""Versioned, checksummed generation manifests and portable source paths."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Mapping

from ._base import (
    ContractValidationError,
    JsonDto,
    JsonValue,
    mapping_get,
    require_bool,
    require_int,
    require_mapping,
    require_string,
)


GENERATION_SCHEMA_VERSION = 2
_SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class GenerationComponentKind(str, Enum):
    INDEX = "index"
    CUSTOMERS = "customers"


@dataclass(frozen=True, slots=True)
class SourcePath(JsonDto):
    """Portable file identity independent of a platform's mount location."""

    source_id: str
    relative_path: str

    def __post_init__(self) -> None:
        source_id = require_string(self.source_id, "source_id")
        relative_path = require_string(self.relative_path, "relative_path")
        if not _SOURCE_ID_PATTERN.fullmatch(source_id):
            raise ContractValidationError(
                "source_id must contain 1-64 ASCII letters, digits, '.', '_' or '-'"
            )
        if "\\" in relative_path or "\x00" in relative_path:
            raise ContractValidationError("relative_path must be a NUL-free POSIX relative path")
        path = PurePosixPath(relative_path)
        if (
            path.is_absolute()
            or relative_path.startswith("/")
            or relative_path.endswith("/")
            or "//" in relative_path
            or ":" in relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != relative_path
        ):
            raise ContractValidationError(
                "relative_path must be a normalized POSIX path without traversal"
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "SourcePath":
        payload = require_mapping(payload, "source_path")
        return cls(
            source_id=require_string(mapping_get(payload, "source_id", ""), "source_id"),
            relative_path=require_string(
                mapping_get(
                    payload,
                    "relative_path",
                    mapping_get(payload, "path", ""),
                ),
                "relative_path",
            ),
        )


@dataclass(frozen=True, slots=True)
class GenerationComponentManifest(JsonDto):
    kind: GenerationComponentKind
    generation: str
    created_at: str
    archive: str
    size: int
    sha256: str
    content_type: str = "application/zip"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GenerationComponentKind):
            try:
                kind = GenerationComponentKind(str(self.kind))
            except ValueError as exc:
                raise ContractValidationError("unknown generation component kind") from exc
            object.__setattr__(self, "kind", kind)
        generation = require_string(self.generation, "generation")
        created_at = require_string(self.created_at, "created_at")
        archive = require_string(self.archive, "archive")
        require_int(self.size, "size", minimum=0)
        sha256 = require_string(self.sha256, "sha256")
        require_string(self.content_type, "content_type")
        if not _GENERATION_PATTERN.fullmatch(generation):
            raise ContractValidationError("generation must be a safe identifier")
        archive_path = PurePosixPath(archive)
        if (
            archive.startswith("//")
            or "//" in archive
            or ":" in archive
            or "\\" in archive
            or "\x00" in archive
            or any(part in {"", ".", ".."} for part in archive_path.parts)
            or archive_path.as_posix() != archive
        ):
            raise ContractValidationError(
                "archive must be a safe relative path or server-local API path"
            )
        if not _SHA256_PATTERN.fullmatch(sha256):
            raise ContractValidationError("sha256 must contain exactly 64 hex digits")
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "archive", archive)
        object.__setattr__(self, "sha256", sha256.lower())

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, object],
        *,
        default_kind: GenerationComponentKind | None = None,
    ) -> "GenerationComponentManifest":
        payload = require_mapping(payload, "generation_component")
        raw_kind = mapping_get(payload, "kind", default_kind)
        if isinstance(raw_kind, GenerationComponentKind):
            kind = raw_kind
        else:
            try:
                kind = GenerationComponentKind(require_string(raw_kind, "kind"))
            except ValueError as exc:
                raise ContractValidationError("unknown generation component kind") from exc
        return cls(
            kind=kind,
            generation=require_string(mapping_get(payload, "generation", ""), "generation"),
            created_at=require_string(mapping_get(payload, "created_at", ""), "created_at"),
            archive=require_string(mapping_get(payload, "archive", ""), "archive"),
            size=require_int(mapping_get(payload, "size", -1), "size", minimum=0),
            sha256=require_string(mapping_get(payload, "sha256", ""), "sha256"),
            content_type=require_string(
                mapping_get(payload, "content_type", "application/zip"),
                "content_type",
            ),
        )


@dataclass(frozen=True, slots=True)
class GenerationManifest(JsonDto):
    created_at: str
    index: GenerationComponentManifest | None
    customers: GenerationComponentManifest | None
    schema_version: int = GENERATION_SCHEMA_VERSION
    legacy_combined: bool = False

    def __post_init__(self) -> None:
        require_string(self.created_at, "created_at")
        require_int(self.schema_version, "schema_version", minimum=1, maximum=2)
        require_bool(self.legacy_combined, "legacy_combined")
        if self.index is None and self.customers is None:
            raise ContractValidationError(
                "a generation manifest must contain at least one component"
            )
        if self.index is not None and not isinstance(self.index, GenerationComponentManifest):
            raise ContractValidationError("index must be a component manifest or null")
        if self.customers is not None and not isinstance(
            self.customers, GenerationComponentManifest
        ):
            raise ContractValidationError("customers must be a component manifest or null")
        if self.index is not None and self.index.kind is not GenerationComponentKind.INDEX:
            raise ContractValidationError("index component has the wrong kind")
        if (
            self.customers is not None
            and self.customers.kind is not GenerationComponentKind.CUSTOMERS
        ):
            raise ContractValidationError("customers component has the wrong kind")
        if self.schema_version == 1 and not self.legacy_combined:
            raise ContractValidationError(
                "schema version 1 must be marked as a legacy combined generation"
            )
        if self.schema_version == 1 and (
            self.index is None
            or self.customers is None
            or self.index.generation != self.customers.generation
            or self.index.archive != self.customers.archive
            or self.index.size != self.customers.size
            or self.index.sha256 != self.customers.sha256
        ):
            raise ContractValidationError(
                "schema version 1 must reference one shared component archive"
            )
        if self.schema_version == 2 and self.legacy_combined:
            raise ContractValidationError(
                "schema version 2 cannot be marked as a legacy combined generation"
            )

    def component(self, kind: GenerationComponentKind | str) -> GenerationComponentManifest | None:
        if not isinstance(kind, GenerationComponentKind):
            kind = GenerationComponentKind(kind)
        return self.index if kind is GenerationComponentKind.INDEX else self.customers

    def to_dict(self) -> dict[str, JsonValue]:
        if self.schema_version == 1:
            # Preserve the wire shape understood by v1 clients. Both component
            # DTOs point at this same archive when parsed.
            assert self.index is not None
            return {
                "schema_version": 1,
                "generation": self.index.generation,
                "created_at": self.created_at,
                "archive": self.index.archive,
                "size": self.index.size,
                "sha256": self.index.sha256,
                "content_type": self.index.content_type,
            }
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "legacy_combined": self.legacy_combined,
            "components": {
                GenerationComponentKind.INDEX.value: (
                    self.index.to_dict() if self.index is not None else None
                ),
                GenerationComponentKind.CUSTOMERS.value: (
                    self.customers.to_dict() if self.customers is not None else None
                ),
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "GenerationManifest":
        payload = require_mapping(payload, "generation_manifest")
        schema_version = require_int(
            mapping_get(payload, "schema_version", 1),
            "schema_version",
            minimum=1,
            maximum=2,
        )
        if schema_version == 1:
            return cls._from_v1(payload)
        # Early v2 implementations briefly emitted `index` and `customers` at
        # the top level. Accept that shape but always serialize `components`.
        components_value = mapping_get(payload, "components", payload)
        components = require_mapping(components_value, "components")
        index_payload = mapping_get(components, GenerationComponentKind.INDEX.value, None)
        index = (
            GenerationComponentManifest.from_dict(
                require_mapping(index_payload, "components.index"),
                default_kind=GenerationComponentKind.INDEX,
            )
            if index_payload is not None
            else None
        )
        customers_payload = mapping_get(
            components,
            GenerationComponentKind.CUSTOMERS.value,
            mapping_get(components, "customer", {}),
        )
        customers = (
            GenerationComponentManifest.from_dict(
                require_mapping(customers_payload, "components.customers"),
                default_kind=GenerationComponentKind.CUSTOMERS,
            )
            if customers_payload is not None
            else None
        )
        fallback_created_at = (
            index.created_at
            if index is not None
            else customers.created_at
            if customers is not None
            else ""
        )
        return cls(
            created_at=require_string(
                mapping_get(payload, "created_at", fallback_created_at), "created_at"
            ),
            index=index,
            customers=customers,
            schema_version=2,
        )

    @classmethod
    def _from_v1(cls, payload: Mapping[str, object]) -> "GenerationManifest":
        common = {
            "generation": mapping_get(payload, "generation", ""),
            "created_at": mapping_get(payload, "created_at", ""),
            "archive": mapping_get(payload, "archive", ""),
            "size": mapping_get(payload, "size", -1),
            "sha256": mapping_get(payload, "sha256", ""),
            "content_type": mapping_get(payload, "content_type", "application/zip"),
        }
        created_at = require_string(common["created_at"], "created_at")
        return cls(
            created_at=created_at,
            index=GenerationComponentManifest.from_dict(
                common, default_kind=GenerationComponentKind.INDEX
            ),
            customers=GenerationComponentManifest.from_dict(
                common, default_kind=GenerationComponentKind.CUSTOMERS
            ),
            schema_version=1,
            legacy_combined=True,
        )


GenerationComponent = GenerationComponentManifest
