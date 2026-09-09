"""Presentation policy for the complete server-owned index configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from papagui_contracts import IndexSettings


@dataclass(frozen=True, slots=True)
class ServerSettingsViewModel:
    values: Mapping[str, object]
    interval_value: int
    interval_unit: str


class ServerSettingsPresenter:
    FIELDS = (
        "automatic_runs_enabled",
        "interval_seconds",
        "daily_reconciliation_enabled",
        "content_indexing_enabled",
        "content_extensions",
        "excluded_folders",
        "max_file_size_mb",
        "max_extracted_characters",
        "ocr_enabled",
        "ocr_max_pages",
        "ocr_extended_max_pages",
        "ocr_extension_threshold",
        "ocr_timeout_seconds",
        "pdf_text_timeout_seconds",
        "resource_profile",
        "preferred_document_patterns",
        "priority_documents_per_project",
        "newest_years_first",
        "minimum_customer_year",
        "recognition_pipeline_enabled",
        "recognition_own_names",
        "recognition_documents_per_project_max",
        "extraction_timeout_seconds",
        "extraction_memory_mb",
        "pdf_max_pages",
        "image_max_pixels",
        "extraction_retry_attempts",
        "extraction_retry_delay_seconds",
        "extraction_store_max_mb",
        "extraction_retention_days",
    )

    def present(self, response: Mapping[str, object]) -> ServerSettingsViewModel:
        raw = response.get("settings", response)
        if not isinstance(raw, Mapping):
            raise ValueError("Servereinstellungen müssen ein Objekt sein")
        values = self.validate(raw)
        value, unit = self.interval_fields(int(values["interval_seconds"]))
        return ServerSettingsViewModel(values, value, unit)

    def validate(self, values: Mapping[str, object]) -> dict[str, object]:
        contract = IndexSettings.from_dict(values)
        normalized = dict(contract.to_dict())
        year = values.get("minimum_customer_year", 2016)
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 9999:
            raise ValueError("Das minimale Kundenjahr muss zwischen 1900 und 9999 liegen")
        normalized["minimum_customer_year"] = year
        return {name: normalized[name] for name in self.FIELDS}

    @staticmethod
    def interval_fields(seconds: int) -> tuple[int, str]:
        seconds = max(900, min(172_800, seconds))
        if seconds % 3600 == 0:
            return seconds // 3600, "Stunden"
        return seconds // 60, "Minuten"

    @staticmethod
    def interval_seconds(value: int, unit: str) -> int:
        if unit not in {"Minuten", "Stunden"}:
            raise ValueError("Unbekannte Intervalleinheit")
        seconds = value * (3600 if unit == "Stunden" else 60)
        if not 900 <= seconds <= 172_800:
            raise ValueError("Intervall muss zwischen 15 Minuten und 48 Stunden liegen")
        return seconds
