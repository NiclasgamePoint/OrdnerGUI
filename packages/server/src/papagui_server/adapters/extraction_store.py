"""Private extraction cache, deliberately outside portable index/customer stores."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import shutil
from pathlib import Path
import sqlite3
import time
from typing import Iterable

from papagui_server.domain.document_extraction import ExtractionResult

RETRYABLE = frozenset({"tool_missing", "timeout", "error"})


def artifact_identity(result: ExtractionResult) -> str:
    payload = result.to_dict()
    payload.pop("artifact_hash", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ExtractionStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connection(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS document_extractions (
                content_hash TEXT NOT NULL, version_hash TEXT NOT NULL,
                result_json TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL, next_retry REAL NOT NULL,
                created_at REAL NOT NULL, touched_at REAL NOT NULL,
                PRIMARY KEY(content_hash, version_hash))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS immutable_document_extractions (
                content_hash TEXT NOT NULL, artifact_hash TEXT NOT NULL,
                result_json TEXT NOT NULL, touched_at REAL NOT NULL,
                PRIMARY KEY(content_hash, artifact_hash))""")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get(
        self, content_hash: str, version_hash: str, *, max_attempts: int = 3
    ) -> ExtractionResult | None:
        if not self.database_path.exists():
            return None
        with self._connection() as connection:
            row = connection.execute(
                "SELECT result_json, status, attempts, next_retry FROM document_extractions WHERE content_hash=? AND version_hash=?",
                (content_hash, version_hash),
            ).fetchone()
            if row is None:
                return None
            try:
                parsed = ExtractionResult.from_dict(json.loads(row["result_json"]))
            except (ValueError, TypeError, KeyError):
                return None
            if parsed.reason == "cancelled":
                return None
            retryable = row["status"] in RETRYABLE or any(
                page.status in RETRYABLE for page in parsed.pages
            )
            if retryable and row["attempts"] < max_attempts and row["next_retry"] <= time.time():
                return None
            connection.execute(
                "UPDATE document_extractions SET touched_at=? WHERE content_hash=? AND version_hash=?",
                (time.time(), content_hash, version_hash),
            )
        return parsed

    def put(
        self, result: ExtractionResult, *, retry_delay_seconds: int = 300, max_store_mb: int = 1024
    ) -> bool:
        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        maximum = max_store_mb * 1024 * 1024
        with self._connection() as connection:
            size = int(
                connection.execute(
                    "SELECT COALESCE(SUM(length(CAST(result_json AS BLOB))), 0) FROM document_extractions WHERE NOT (content_hash=? AND version_hash=?)",
                    (result.content_hash, result.version_hash),
                ).fetchone()[0]
            )
            immutable_size = int(
                connection.execute(
                    "SELECT COALESCE(SUM(length(CAST(result_json AS BLOB))), 0) FROM immutable_document_extractions"
                ).fetchone()[0]
            )
            if (
                size + immutable_size + 2 * len(payload.encode()) > maximum
                or shutil.disk_usage(self.database_path.parent).free
                < 2 * len(payload.encode()) + 4 * 1024 * 1024
            ):
                return False
            now = time.time()
            connection.execute(
                "INSERT OR IGNORE INTO immutable_document_extractions VALUES (?, ?, ?, ?)",
                (
                    result.content_hash,
                    result.artifact_hash or artifact_identity(result),
                    payload,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO document_extractions (
                content_hash, version_hash, result_json, status, attempts,
                next_retry, created_at, touched_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(content_hash, version_hash) DO UPDATE SET
                result_json=excluded.result_json, status=excluded.status,
                attempts=document_extractions.attempts+1,
                next_retry=excluded.next_retry, touched_at=excluded.touched_at""",
                (
                    result.content_hash,
                    result.version_hash,
                    payload,
                    result.status,
                    now + retry_delay_seconds,
                    now,
                    now,
                ),
            )
        return True

    def read(self, content_hash: str, version_hash: str) -> ExtractionResult | None:
        """Read exact immutable artifacts without scheduling retries or writes."""
        if not self.database_path.is_file():
            return None
        connection = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            row = (
                connection.execute(
                    "SELECT result_json FROM immutable_document_extractions WHERE content_hash=? AND artifact_hash=?",
                    (content_hash, version_hash),
                ).fetchone()
                if "immutable_document_extractions" in tables
                else None
            )
            if row is None:
                row = connection.execute(
                    "SELECT result_json FROM document_extractions WHERE content_hash=? AND version_hash=?",
                    (content_hash, version_hash),
                ).fetchone()
            return ExtractionResult.from_dict(json.loads(row[0])) if row else None
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return None
        finally:
            connection.close()

    def cleanup(self, *, protected: Iterable[tuple[str, str]], retention_days: int = 30) -> int:
        """Caller supplies references from active and retained catalog snapshots."""
        if not self.database_path.exists():
            return 0
        with self._connection() as connection:
            connection.execute(
                "CREATE TEMP TABLE protected (content_hash TEXT, version_hash TEXT, PRIMARY KEY(content_hash,version_hash))"
            )
            connection.executemany("INSERT OR IGNORE INTO protected VALUES (?,?)", protected)
            result = connection.execute(
                """DELETE FROM document_extractions
                WHERE touched_at < ? AND NOT EXISTS (
                SELECT 1 FROM protected p WHERE p.content_hash=document_extractions.content_hash
                AND (p.version_hash=document_extractions.version_hash
                OR p.version_hash=json_extract(document_extractions.result_json, '$.artifact_hash')))""",
                (time.time() - retention_days * 86400,),
            )
            removed = result.rowcount
            connection.execute(
                """DELETE FROM immutable_document_extractions WHERE touched_at < ?
                AND NOT EXISTS (SELECT 1 FROM protected p WHERE p.content_hash=immutable_document_extractions.content_hash
                    AND p.version_hash=immutable_document_extractions.artifact_hash)
                AND NOT EXISTS (SELECT 1 FROM document_extractions d WHERE d.content_hash=immutable_document_extractions.content_hash
                    AND json_extract(d.result_json,'$.artifact_hash')=immutable_document_extractions.artifact_hash)
                """,
                (time.time() - retention_days * 86400,),
            )
            return removed
