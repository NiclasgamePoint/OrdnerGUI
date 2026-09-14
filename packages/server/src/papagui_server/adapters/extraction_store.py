"""Private extraction cache, deliberately outside portable index/customer stores."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import shutil
from pathlib import Path
import sqlite3
import threading
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
        self._writes = threading.local()

    def peek(
        self, content_hash: str, version_hash: str, *, max_attempts: int = 3
    ) -> ExtractionResult | None:
        """Read without schema/timestamp writes; safe for parallel document readers."""
        if not self.database_path.is_file():
            return None
        connection = sqlite3.connect(self.database_path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT result_json,status,attempts,next_retry FROM document_extractions WHERE content_hash=? AND version_hash=?",
                (content_hash, version_hash),
            ).fetchone()
            if row is None:
                return None
            parsed = ExtractionResult.from_dict(json.loads(row["result_json"]))
            if parsed.reason == "cancelled":
                return None
            retryable = row["status"] in RETRYABLE or any(
                page.status in RETRYABLE for page in parsed.pages
            )
            if retryable and row["attempts"] < max_attempts and row["next_retry"] <= time.time():
                return None
            return parsed
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return None
        finally:
            connection.close()

    @contextmanager
    def _connection(self):
        batch = getattr(self._writes, "batch", None)
        if batch is not None:
            if "connection" not in batch:
                batch["connection"] = self._open_writer()
                # A bounded batch must not take an exclusive spill lock while
                # document readers are waiting for the writer to drain their queue.
                batch["connection"].execute("PRAGMA cache_spill=OFF")
            yield batch["connection"]
            return
        connection = self._open_writer()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def buffered_writes(self):
        """One lazy writer; readers keep using independent read-only connections."""
        if getattr(self._writes, "batch", None) is not None:
            raise RuntimeError("Extraction write batches must not be nested")
        self._writes.batch = batch = {}
        try:
            yield
        except InterruptedError:
            # The catalog also commits completed work on a controlled cancellation.
            self.flush()
            raise
        else:
            self.flush()
        finally:
            if "connection" in batch:
                batch["connection"].close()  # Uncommitted work rolls back on errors.
            del self._writes.batch

    def flush(self) -> None:
        batch = getattr(self._writes, "batch", None)
        if batch is not None and "connection" in batch:
            batch["connection"].commit()
            batch["bytes"] = 0

    def _open_writer(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
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
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='extraction_storage_usage' AND type='table'"
            ).fetchone():
                self._initialize_usage(connection)
            connection.commit()
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _initialize_usage(connection: sqlite3.Connection) -> None:
        # Seed existing caches once, then maintain the quota transactionally.
        # Triggers also cover retries, cleanup, and independent writer connections.
        connection.execute(
            "CREATE TABLE extraction_storage_usage(id INTEGER PRIMARY KEY CHECK(id=1), payload_bytes INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO extraction_storage_usage VALUES (1,0)")
        for table in ("document_extractions", "immutable_document_extractions"):
            connection.execute(
                f"UPDATE extraction_storage_usage SET payload_bytes=payload_bytes+"
                f"(SELECT COALESCE(SUM(length(CAST(result_json AS BLOB))),0) FROM {table}) WHERE id=1"
            )
            for operation, change in (
                ("INSERT", "length(CAST(NEW.result_json AS BLOB))"),
                ("DELETE", "-length(CAST(OLD.result_json AS BLOB))"),
                ("UPDATE OF result_json", "length(CAST(NEW.result_json AS BLOB))-length(CAST(OLD.result_json AS BLOB))"),
            ):
                name = operation.split()[0].lower()
                connection.execute(
                    f"CREATE TRIGGER {table}_usage_{name} AFTER {operation} ON {table} BEGIN "
                    f"UPDATE extraction_storage_usage SET payload_bytes=payload_bytes+({change}) WHERE id=1; END"
                )

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
        payload_size = len(payload.encode("utf-8"))
        artifact_hash = result.artifact_hash or artifact_identity(result)
        maximum = max_store_mb * 1024 * 1024
        with self._connection() as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            size = connection.execute(
                "SELECT payload_bytes FROM extraction_storage_usage WHERE id=1"
            ).fetchone()[0]
            previous = connection.execute(
                "SELECT length(CAST(result_json AS BLOB)) FROM document_extractions WHERE content_hash=? AND version_hash=?",
                (result.content_hash, result.version_hash),
            ).fetchone()
            immutable = connection.execute(
                "SELECT 1 FROM immutable_document_extractions WHERE content_hash=? AND artifact_hash=?",
                (result.content_hash, artifact_hash),
            )
            growth = payload_size - (previous[0] if previous else 0)
            growth += 0 if immutable.fetchone() else payload_size
            if (
                size + growth > maximum
                or shutil.disk_usage(self.database_path.parent).free
                < 2 * payload_size + 4 * 1024 * 1024
            ):
                return False
            now = time.time()
            connection.execute(
                "INSERT OR IGNORE INTO immutable_document_extractions VALUES (?, ?, ?, ?)",
                (
                    result.content_hash,
                    artifact_hash,
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
            batch = getattr(self._writes, "batch", None)
            if batch is not None:
                batch["bytes"] = batch.get("bytes", 0) + 2 * payload_size
                if batch["bytes"] >= 1024 * 1024:
                    self.flush()
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
