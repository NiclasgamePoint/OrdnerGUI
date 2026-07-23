from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class ApplicationStatistics:
    customer_count: int = 0
    contact_count: int = 0
    project_count: int = 0
    service_count: int = 0
    file_count: int = 0
    total_file_size: int = 0
    content_count: int = 0
    pending_recognition_count: int = 0
    last_indexed_at: str = ""
    last_index_duration_seconds: float = 0.0


class StatisticsService:
    """Read aggregate application metrics without owning GUI concerns."""

    def load(
        self,
        index_path: Path,
        customer_database_path: Path,
    ) -> ApplicationStatistics:
        customer_values = self._customer_statistics(customer_database_path)
        index_values = self._index_statistics(index_path)
        return ApplicationStatistics(**customer_values, **index_values)

    @staticmethod
    def _readonly_connection(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _customer_statistics(self, path: Path) -> dict:
        values = {
            "customer_count": 0,
            "contact_count": 0,
            "service_count": 0,
            "pending_recognition_count": 0,
        }
        if not path.exists():
            return values
        connection = self._readonly_connection(path)
        try:
            queries = {
                "customer_count": "SELECT COUNT(*) FROM customers",
                "contact_count": "SELECT COUNT(*) FROM contacts",
                "service_count": "SELECT COUNT(*) FROM service_types",
                "pending_recognition_count": (
                    "SELECT COUNT(*) FROM recognition_cases WHERE status='pending'"
                ),
            }
            for key, query in queries.items():
                values[key] = int(connection.execute(query).fetchone()[0])
            return values
        finally:
            connection.close()

    def _index_statistics(self, path: Path) -> dict:
        values = {
            "project_count": 0,
            "file_count": 0,
            "total_file_size": 0,
            "content_count": 0,
            "last_indexed_at": "",
            "last_index_duration_seconds": 0.0,
        }
        if not path.exists():
            return values
        connection = self._readonly_connection(path)
        try:
            file_row = connection.execute(
                "SELECT COUNT(*) AS amount, COALESCE(SUM(file_size), 0) AS size "
                "FROM files"
            ).fetchone()
            metadata = dict(
                connection.execute("SELECT key, value FROM index_metadata")
            )
            values.update({
                "file_count": int(file_row["amount"]),
                "project_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM project_roots"
                    ).fetchone()[0]
                ),
                "total_file_size": int(file_row["size"]),
                "content_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM file_content_fts"
                    ).fetchone()[0]
                ),
                "last_indexed_at": str(metadata.get("built_at") or ""),
                "last_index_duration_seconds": float(
                    metadata.get("duration_seconds") or 0
                ),
            })
            return values
        finally:
            connection.close()
