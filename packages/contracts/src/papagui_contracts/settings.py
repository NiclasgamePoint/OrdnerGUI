"""Server-owned index configuration contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from ._base import (
    ContractValidationError,
    JsonDto,
    mapping_get,
    require_bool,
    require_int,
    require_mapping,
    require_string,
)


MIN_INDEX_INTERVAL_SECONDS = 15 * 60
MAX_INDEX_INTERVAL_SECONDS = 48 * 60 * 60


class ResourceProfile(str, Enum):
    GENTLE = "gentle"
    BALANCED = "balanced"
    FAST = "fast"


@dataclass(frozen=True, slots=True)
class IndexSettings(JsonDto):
    """Settings that affect a server-side index run.

    Client-only preferences such as the remote download interval deliberately do
    not belong here. `automatic_runs_enabled` replaces the ambiguous v1 field
    `automatic_monitoring_enabled`; the v1 spelling is accepted while parsing.
    """

    interval_seconds: int = 24 * 60 * 60
    automatic_runs_enabled: bool = True
    daily_reconciliation_enabled: bool = True
    content_indexing_enabled: bool = True
    max_file_size_mb: int = 100
    max_extracted_characters: int = 2_000_000
    ocr_enabled: bool = True
    ocr_max_pages: int = 5
    ocr_extended_max_pages: int = 25
    ocr_extension_threshold: int = 500
    ocr_timeout_seconds: int = 10
    pdf_text_timeout_seconds: int = 45
    content_extensions: str = "pdf,doc,docx,xls,xlsx,txt,csv,md,log,json,xml,yaml,yml,ini"
    excluded_folders: str = ".git,.venv,venv,__pycache__,node_modules"
    resource_profile: ResourceProfile = ResourceProfile.BALANCED
    preferred_document_patterns: str = "anschreiben,angebot,auftrag,vertrag"
    priority_documents_per_project: int = 24
    newest_years_first: bool = True

    def __post_init__(self) -> None:
        require_int(
            self.interval_seconds,
            "interval_seconds",
            minimum=MIN_INDEX_INTERVAL_SECONDS,
            maximum=MAX_INDEX_INTERVAL_SECONDS,
        )
        require_bool(self.automatic_runs_enabled, "automatic_runs_enabled")
        require_bool(self.daily_reconciliation_enabled, "daily_reconciliation_enabled")
        require_bool(self.content_indexing_enabled, "content_indexing_enabled")
        require_int(self.max_file_size_mb, "max_file_size_mb", minimum=1)
        require_int(
            self.max_extracted_characters,
            "max_extracted_characters",
            minimum=1,
        )
        require_bool(self.ocr_enabled, "ocr_enabled")
        require_int(self.ocr_max_pages, "ocr_max_pages", minimum=1)
        require_int(
            self.ocr_extended_max_pages,
            "ocr_extended_max_pages",
            minimum=self.ocr_max_pages,
        )
        require_int(self.ocr_extension_threshold, "ocr_extension_threshold", minimum=0)
        require_int(self.ocr_timeout_seconds, "ocr_timeout_seconds", minimum=1)
        require_int(self.pdf_text_timeout_seconds, "pdf_text_timeout_seconds", minimum=1)
        require_string(self.content_extensions, "content_extensions")
        require_string(self.excluded_folders, "excluded_folders", allow_empty=True)
        require_string(
            self.preferred_document_patterns,
            "preferred_document_patterns",
            allow_empty=True,
        )
        require_int(
            self.priority_documents_per_project,
            "priority_documents_per_project",
            minimum=0,
        )
        require_bool(self.newest_years_first, "newest_years_first")
        if not isinstance(self.resource_profile, ResourceProfile):
            try:
                profile = ResourceProfile(str(self.resource_profile))
            except ValueError as exc:
                raise ContractValidationError(
                    "resource_profile must be gentle, balanced, or fast"
                ) from exc
            object.__setattr__(self, "resource_profile", profile)

    @property
    def automatic_monitoring_enabled(self) -> bool:
        """Read-only compatibility alias for the v1 setting name."""

        return self.automatic_runs_enabled

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "IndexSettings":
        payload = require_mapping(payload, "index_settings")
        defaults = cls()

        def integer(name: str, minimum: int, maximum: int | None = None) -> int:
            return require_int(
                mapping_get(payload, name, getattr(defaults, name)),
                name,
                minimum=minimum,
                maximum=maximum,
            )

        def boolean(name: str) -> bool:
            return require_bool(mapping_get(payload, name, getattr(defaults, name)), name)

        auto = mapping_get(
            payload,
            "automatic_runs_enabled",
            mapping_get(
                payload,
                "automatic_monitoring_enabled",
                defaults.automatic_runs_enabled,
            ),
        )
        profile_value = mapping_get(payload, "resource_profile", defaults.resource_profile.value)
        try:
            profile = ResourceProfile(require_string(profile_value, "resource_profile"))
        except ValueError as exc:
            raise ContractValidationError(
                "resource_profile must be gentle, balanced, or fast"
            ) from exc
        return cls(
            interval_seconds=integer(
                "interval_seconds",
                MIN_INDEX_INTERVAL_SECONDS,
                MAX_INDEX_INTERVAL_SECONDS,
            ),
            automatic_runs_enabled=require_bool(auto, "automatic_runs_enabled"),
            daily_reconciliation_enabled=boolean("daily_reconciliation_enabled"),
            content_indexing_enabled=boolean("content_indexing_enabled"),
            max_file_size_mb=integer("max_file_size_mb", 1),
            max_extracted_characters=integer("max_extracted_characters", 1),
            ocr_enabled=boolean("ocr_enabled"),
            ocr_max_pages=integer("ocr_max_pages", 1),
            ocr_extended_max_pages=integer("ocr_extended_max_pages", 1),
            ocr_extension_threshold=integer("ocr_extension_threshold", 0),
            ocr_timeout_seconds=integer("ocr_timeout_seconds", 1),
            pdf_text_timeout_seconds=integer("pdf_text_timeout_seconds", 1),
            content_extensions=require_string(
                mapping_get(payload, "content_extensions", defaults.content_extensions),
                "content_extensions",
            ),
            excluded_folders=require_string(
                mapping_get(payload, "excluded_folders", defaults.excluded_folders),
                "excluded_folders",
                allow_empty=True,
            ),
            resource_profile=profile,
            preferred_document_patterns=require_string(
                mapping_get(
                    payload,
                    "preferred_document_patterns",
                    defaults.preferred_document_patterns,
                ),
                "preferred_document_patterns",
                allow_empty=True,
            ),
            priority_documents_per_project=integer("priority_documents_per_project", 0),
            newest_years_first=boolean("newest_years_first"),
        )
