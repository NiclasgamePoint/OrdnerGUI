from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata


MINIMUM_CUSTOMER_YEAR = 2016


def normalize_identity(value: str) -> str:
    """Return a stable comparison key for names and places."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = re.sub(r"[^\wäöüß]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


@dataclass(frozen=True)
class ProjectRoot:
    path: str
    relative_path: str
    service_type: str
    year: int
    customer_label: str
    customer_name: str
    city: str
    recognition_key: str


class FolderStructureClassifier:
    """Classify canonical customer roots in service/year/customer structures."""

    def __init__(self, minimum_year: int = MINIMUM_CUSTOMER_YEAR):
        self.minimum_year = minimum_year

    def classify(self, path: Path | str, index_root: Path | str) -> ProjectRoot | None:
        candidate = Path(path)
        root = Path(index_root)
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None

        parts = relative.parts
        if len(parts) < 3:
            return None
        service_type, year_value, customer_label = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
        )
        if not service_type or not customer_label or not re.fullmatch(r"\d{4}", year_value):
            return None
        year = int(year_value)
        if year < self.minimum_year:
            return None

        if "," in customer_label:
            customer_name, city = (
                value.strip() for value in customer_label.split(",", 1)
            )
        else:
            customer_name, city = customer_label, ""
        if not customer_name:
            return None

        root_relative = Path(service_type) / year_value / customer_label
        root_path = root / root_relative
        recognition_key = "|".join(
            (normalize_identity(customer_name), normalize_identity(city))
        )
        return ProjectRoot(
            path=str(root_path),
            relative_path=str(root_relative),
            service_type=service_type,
            year=year,
            customer_label=customer_label,
            customer_name=customer_name,
            city=city,
            recognition_key=recognition_key,
        )
