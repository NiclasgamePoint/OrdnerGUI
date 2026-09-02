"""Filesystem scanning and persisted resume-match policy."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
import os
from pathlib import Path


class CatalogSourceScanner:
    def files(
        self,
        root: Path,
        excluded: frozenset[str],
        *,
        newest_years_first: bool,
    ) -> Iterator[Path]:
        for current, directories, filenames in os.walk(root, followlinks=False):
            available = [
                value for value in directories if value.casefold() not in excluded
            ]
            depth = len(Path(current).relative_to(root).parts)
            directories[:] = sorted(
                available,
                key=lambda value: value.casefold(),
                reverse=newest_years_first and depth == 1,
            )
            for filename in sorted(filenames):
                path = Path(current) / filename
                if path.is_file() and not path.is_symlink():
                    yield path


class CatalogResumeMatcher:
    def matches(
        self,
        catalog_path: Path,
        state_path: Path,
        *,
        source_path: Path,
        source_id: str,
        settings_fingerprint: str,
        catalog_is_valid: Callable[[Path], bool],
    ) -> bool:
        if not catalog_is_valid(catalog_path):
            return False
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return False
        return payload == {
            **payload,
            "source_id": source_id,
            "source_path": str(source_path),
            "settings_fingerprint": settings_fingerprint,
        }
