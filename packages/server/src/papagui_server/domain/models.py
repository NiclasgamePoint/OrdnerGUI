"""Small immutable values used by the server application layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from papagui_contracts import IndexSettings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Durable settings owned by the server, never by a desktop client."""

    interval_seconds: int = 86_400
    automatic_runs_enabled: bool = True
    daily_reconciliation_enabled: bool = True
    content_indexing_enabled: bool = True
    minimum_customer_year: int = 2016
    max_file_size_mb: int = 100
    max_extracted_characters: int = 2_000_000
    content_extensions: str = (
        "pdf,doc,docx,xls,xlsx,txt,csv,md,log,json,xml,yaml,yml,ini,jpg,jpeg,png,tif,tiff,bmp,webp"
    )
    excluded_folders: str = ".git,.venv,venv,__pycache__,node_modules"
    ocr_enabled: bool = True
    ocr_max_pages: int = 5
    ocr_extended_max_pages: int = 25
    ocr_extension_threshold: int = 500
    ocr_timeout_seconds: int = 10
    pdf_text_timeout_seconds: int = 45
    resource_profile: str = "balanced"
    preferred_document_patterns: str = "anschreiben,angebot,auftrag,vertrag"
    priority_documents_per_project: int = 24
    newest_years_first: bool = True
    extraction_timeout_seconds: int = 90
    extraction_memory_mb: int = 768
    pdf_max_pages: int = 200
    image_max_pixels: int = 25000000
    extraction_retry_attempts: int = 3
    extraction_retry_delay_seconds: int = 300
    extraction_store_max_mb: int = 1024
    extraction_retention_days: int = 30
    recognition_documents_per_project_max: int = 500
    recognition_pipeline_enabled: bool = True
    recognition_own_names: str = ''

    def __post_init__(self) -> None:
        # The shared contract is the canonical type/range validator. This prevents
        # Python coercions such as `False == 0` and keeps API/client semantics equal.
        IndexSettings.from_dict(
            {
                key: value
                for key, value in asdict(self).items()
                if key != "minimum_customer_year"
            }
        )
        if (
            isinstance(self.minimum_customer_year, bool)
            or not isinstance(self.minimum_customer_year, int)
            or not 1900 <= self.minimum_customer_year <= 9999
        ):
            raise ValueError("Das minimale Kundenjahr ist ungültig.")

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "ServerSettings":
        allowed = cls.__dataclass_fields__
        return cls(**{key: values[key] for key in allowed if key in values})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def excluded_folder_names(self) -> frozenset[str]:
        return frozenset(
            value.strip().casefold()
            for value in self.excluded_folders.split(",")
            if value.strip()
        )

    @property
    def indexed_content_types(self) -> frozenset[str]:
        return frozenset(
            value.strip().lower().lstrip(".")
            for value in self.content_extensions.split(",")
            if value.strip()
        )

    @property
    def automatic_run_enabled(self) -> bool:
        """Compatibility alias for early server-v2 implementations."""
        return self.automatic_runs_enabled


@dataclass(frozen=True, slots=True)
class IndexRunSnapshot:
    run_id: str = ""
    state: str = "idle"
    phase: str = "idle"
    processed_count: int = 0
    current_path: str = ""
    full_rebuild: bool = False
    started_at: str = ""
    completed_at: str = ""
    error: str = ""
    queued_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CustomerMutationResult:
    body: dict[str, Any]
    status_code: int
    replayed: bool = False


@dataclass(slots=True)
class MutableRunState:
    run_id: str = ""
    state: str = "idle"
    phase: str = "idle"
    processed_count: int = 0
    current_path: str = ""
    full_rebuild: bool = False
    started_at: str = ""
    completed_at: str = ""
    error: str = ""
    queued_action: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> IndexRunSnapshot:
        return IndexRunSnapshot(
            run_id=self.run_id,
            state=self.state,
            phase=self.phase,
            processed_count=self.processed_count,
            current_path=self.current_path,
            full_rebuild=self.full_rebuild,
            started_at=self.started_at,
            completed_at=self.completed_at,
            error=self.error,
            queued_action=self.queued_action,
        )
