from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class IndexDiagnostics:
    database_path: str
    database_size: int = 0
    integrity: str = "unbekannt"
    root_path: str = ""
    built_at: str = ""
    duration_seconds: float = 0.0
    build_mode: str = ""
    file_count: int = 0
    folder_count: int = 0
    changed_count: int = 0
    content_count: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)


class IndexDiagnosticsService:
    """Read-only health report for an index database."""

    def inspect(self, database_path: Path) -> IndexDiagnostics:
        if not database_path.exists():
            return IndexDiagnostics(database_path=str(database_path), integrity="fehlt")

        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            metadata = dict(connection.execute("SELECT key, value FROM index_metadata"))
            status_counts = {
                str(row["content_status"] or "unbekannt"): int(row["amount"])
                for row in connection.execute(
                    """
                    SELECT content_status, COUNT(*) AS amount
                    FROM files GROUP BY content_status
                    """
                )
            }
            errors = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT path, content_error AS error
                    FROM files
                    WHERE COALESCE(content_error, '') <> ''
                    ORDER BY path LIMIT 25
                    """
                )
            ]
            return IndexDiagnostics(
                database_path=str(database_path),
                database_size=database_path.stat().st_size,
                integrity=integrity,
                root_path=metadata.get("index_root", ""),
                built_at=metadata.get("built_at", ""),
                duration_seconds=float(metadata.get("duration_seconds", "0") or 0),
                build_mode=metadata.get("build_mode", ""),
                file_count=int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
                folder_count=int(connection.execute("SELECT COUNT(*) FROM folders").fetchone()[0]),
                changed_count=int(metadata.get("changed_count", "0") or 0),
                content_count=int(
                    connection.execute("SELECT COUNT(*) FROM file_content_fts").fetchone()[0]
                ),
                status_counts=status_counts,
                errors=errors,
            )
        finally:
            connection.close()
