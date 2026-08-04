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
    content_database_size: int = 0
    shard_count: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)


class IndexDiagnosticsService:
    """Read-only health report for an index database."""

    def inspect(
        self,
        database_path: Path,
        content_state_path: Path | None = None,
    ) -> IndexDiagnostics:
        if not database_path.exists():
            return IndexDiagnostics(database_path=str(database_path), integrity="fehlt")

        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            metadata = dict(connection.execute("SELECT key, value FROM index_metadata"))
            content_count, status_counts, errors = self._content_diagnostics(
                connection, content_state_path
            )
            shard_paths = (
                list((content_state_path.parent / "shards").glob("*.db"))
                if content_state_path is not None else []
            )
            content_size = sum(
                path.stat().st_size for path in shard_paths if path.is_file()
            )
            if content_state_path is not None and content_state_path.exists():
                content_size += content_state_path.stat().st_size
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
                content_count=content_count,
                content_database_size=content_size,
                shard_count=len(shard_paths),
                status_counts=status_counts,
                errors=errors,
            )
        finally:
            connection.close()

    def _content_diagnostics(
        self,
        catalog: sqlite3.Connection,
        state_path: Path | None,
    ) -> tuple[int, dict[str, int], list[dict[str, str]]]:
        if state_path is not None and state_path.exists():
            state = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
            state.row_factory = sqlite3.Row
            try:
                statuses = {
                    str(row["content_status"] or row["status"]): int(row["amount"])
                    for row in state.execute(
                        "SELECT status,content_status,COUNT(*) amount FROM documents "
                        "GROUP BY status,content_status"
                    )
                }
                errors = [
                    {"path": str(row["path"]), "error": str(row["content_error"])}
                    for row in state.execute(
                        "SELECT path,content_error FROM documents "
                        "WHERE content_error<>'' ORDER BY path LIMIT 25"
                    )
                ]
                count = int(state.execute(
                    "SELECT COUNT(*) FROM documents WHERE status='completed'"
                ).fetchone()[0])
                return count, statuses, errors
            finally:
                state.close()
        table = catalog.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_content_fts'"
        ).fetchone()
        if table is None:
            return 0, {}, []
        statuses = {
            str(row["content_status"] or "unbekannt"): int(row["amount"])
            for row in catalog.execute(
                "SELECT content_status,COUNT(*) amount FROM files GROUP BY content_status"
            )
        }
        errors = [
            dict(row)
            for row in catalog.execute(
                "SELECT path,content_error error FROM files "
                "WHERE COALESCE(content_error,'')<>'' ORDER BY path LIMIT 25"
            )
        ]
        count = int(catalog.execute("SELECT COUNT(*) FROM file_content_fts").fetchone()[0]) if table else 0
        return count, statuses, errors
