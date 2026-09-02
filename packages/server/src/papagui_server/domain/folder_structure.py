"""Pure portable classification of source-relative folder structures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import unicodedata

from papagui_contracts import SourcePath


def normalize_identity(value: str) -> str:
    """Return a stable key for human names without binding it to a platform path."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = re.sub(r"[^\wäöüß]+", " ", normalized)
    return " ".join(normalized.split())


@dataclass(frozen=True, slots=True)
class ProjectRoot:
    source: SourcePath
    service_type: str
    year: int
    customer_label: str
    customer_name: str
    city: str
    recognition_key: str


@dataclass(frozen=True, slots=True)
class FolderMetadata:
    source: SourcePath | None
    name: str
    parent: SourcePath | None
    domain_folder: str
    time_bucket: str
    project_name: str
    project_root: ProjectRoot | None


class FolderStructureClassifier:
    """Classify canonical ``service/year/customer`` project roots."""

    def __init__(self, minimum_year: int = 2016) -> None:
        if isinstance(minimum_year, bool) or not 1900 <= minimum_year <= 9999:
            raise ValueError("Das minimale Kundenjahr ist ungültig.")
        self.minimum_year = minimum_year

    def classify(self, source_id: str, relative_path: str) -> ProjectRoot | None:
        relative = _portable(relative_path)
        parts = PurePosixPath(relative).parts if relative else ()
        if len(parts) < 3:
            return None
        SourcePath(source_id, relative)
        service_type, year_value, customer_label = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
        )
        if (
            not service_type
            or not customer_label
            or not re.fullmatch(r"\d{4}", year_value)
        ):
            return None
        year = int(year_value)
        if year < self.minimum_year:
            return None
        customer_name, separator, city = customer_label.partition(",")
        customer_name = customer_name.strip()
        city = city.strip() if separator else ""
        if not customer_name:
            return None
        root_relative = PurePosixPath(*parts[:3]).as_posix()
        return ProjectRoot(
            source=SourcePath(source_id, root_relative),
            service_type=service_type,
            year=year,
            customer_label=customer_label,
            customer_name=customer_name,
            city=city,
            recognition_key=normalize_identity(customer_name),
        )


class FolderStructureParser:
    """Parse folders and files into stable faceting metadata."""

    def __init__(self, classifier: FolderStructureClassifier) -> None:
        self._classifier = classifier

    def parse_directory(self, source_id: str, relative_path: str) -> FolderMetadata:
        relative = _portable(relative_path)
        source = SourcePath(source_id, relative) if relative else None
        parts = PurePosixPath(relative).parts if relative else ()
        parent_relative = PurePosixPath(*parts[:-1]).as_posix() if len(parts) > 1 else ""
        parent = SourcePath(source_id, parent_relative) if len(parts) > 1 else None
        project_root = self._classifier.classify(source_id, relative)
        return FolderMetadata(
            source=source,
            name=parts[-1] if parts else source_id,
            parent=parent,
            domain_folder=parts[0] if parts else "",
            time_bucket=parts[1] if len(parts) > 1 else "",
            project_name=parts[2] if len(parts) > 2 else "",
            project_root=project_root,
        )

    def parse_file(self, source_id: str, relative_path: str) -> FolderMetadata:
        relative = _portable(relative_path)
        parts = PurePosixPath(relative).parts
        directory = PurePosixPath(*parts[:-1]).as_posix() if len(parts) > 1 else ""
        return self.parse_directory(source_id, directory)


def _portable(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip("/")
    if normalized in {"", "."}:
        return ""
    # SourcePath performs the authoritative traversal/absolute validation.
    return PurePosixPath(normalized).as_posix()
