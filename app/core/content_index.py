from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shutil
import sqlite3
import zlib

from app.core.index_layout import IndexLayout


CONTENT_SCHEMA_VERSION = 1
DEFAULT_SHARD_TARGET_BYTES = 1024**3
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ContentTask:
    document_key: str
    path: str
    source_version: str
    partition_year: int | None
    priority: int
    attempts: int


@dataclass(frozen=True)
class ContentProgress:
    total_documents: int
    completed_documents: int
    pending_documents: int
    failed_documents: int
    total_bytes: int
    completed_bytes: int

    @property
    def complete(self) -> bool:
        return self.pending_documents == 0


@dataclass(frozen=True)
class ContentSearchHit:
    document_key: str
    source_version: str
    path: str
    excerpt: str
    rank: float
    shard_name: str


class ContentStateRepository:
    """Durable queue and shard manifest, independent from catalog activation."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        try:
            self._initialize()
        except Exception:
            self.connection.close()
            raise

    def _initialize(self):
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_key TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                source_version TEXT NOT NULL,
                partition_year INTEGER,
                source_size INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 100,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                lease_until TEXT NOT NULL DEFAULT '',
                shard_name TEXT NOT NULL DEFAULT '',
                content_status TEXT NOT NULL DEFAULT '',
                content_error TEXT NOT NULL DEFAULT '',
                extracted_characters INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                catalog_generation TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_queue
                ON documents(status, priority, partition_year, path);
            CREATE INDEX IF NOT EXISTS idx_content_shard
                ON documents(shard_name);
            CREATE INDEX IF NOT EXISTS idx_content_generation
                ON documents(catalog_generation);
            CREATE TABLE IF NOT EXISTS shards (
                name TEXT PRIMARY KEY,
                partition_year INTEGER,
                sequence INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                document_count INTEGER NOT NULL DEFAULT 0,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(CONTENT_SCHEMA_VERSION),),
        )
        self.connection.commit()

    @classmethod
    def open_recoverable(
        cls,
        layout: IndexLayout,
    ) -> "ContentStateRepository":
        try:
            repository = cls(layout.content_state_path)
            integrity = repository.connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            if integrity != "ok":
                repository.close()
                raise sqlite3.DatabaseError(str(integrity))
            return repository
        except sqlite3.DatabaseError:
            layout.corrupt_shard_dir.mkdir(parents=True, exist_ok=True)
            timestamp = int(datetime.now().timestamp())
            for suffix in ("", "-wal", "-shm"):
                source = Path(f"{layout.content_state_path}{suffix}")
                if source.exists():
                    destination = layout.corrupt_shard_dir / (
                        f"state-{timestamp}.db{suffix}"
                    )
                    shutil.move(source, destination)
            return cls(layout.content_state_path)

    def reconcile_document(
        self,
        *,
        document_key: str,
        path: str,
        source_version: str,
        partition_year: int | None,
        source_size: int,
        priority: int,
        catalog_generation: str,
    ):
        existing = self.connection.execute(
            "SELECT source_version,partition_year,shard_name FROM documents "
            "WHERE document_key=?",
            (document_key,),
        ).fetchone()
        changed = existing is None or existing["source_version"] != source_version
        moved_from = (
            str(existing["shard_name"] or "")
            if existing is not None
            and existing["partition_year"] != partition_year
            else ""
        )
        self.connection.execute(
            """
            INSERT INTO documents(
                document_key, path, source_version, partition_year, source_size,
                priority, status, updated_at, catalog_generation
            ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(document_key) DO UPDATE SET
                path=excluded.path,
                source_version=excluded.source_version,
                partition_year=excluded.partition_year,
                source_size=excluded.source_size,
                priority=excluded.priority,
                status=CASE WHEN documents.source_version<>excluded.source_version
                            THEN 'pending' ELSE documents.status END,
                attempts=CASE WHEN documents.source_version<>excluded.source_version
                              THEN 0 ELSE documents.attempts END,
                content_error=CASE WHEN documents.source_version<>excluded.source_version
                                   THEN '' ELSE documents.content_error END,
                shard_name=CASE
                    WHEN documents.partition_year IS NOT excluded.partition_year THEN ''
                    ELSE documents.shard_name END,
                updated_at=excluded.updated_at,
                catalog_generation=excluded.catalog_generation
            """,
            (
                document_key, path, source_version, partition_year, source_size,
                priority, utc_now(), catalog_generation,
            ),
        )
        return moved_from, changed

    def finalize_generation(self, catalog_generation: str) -> list[sqlite3.Row]:
        stale = self.connection.execute(
            "SELECT document_key, shard_name FROM documents "
            "WHERE catalog_generation<>?",
            (catalog_generation,),
        ).fetchall()
        self.connection.execute(
            "DELETE FROM documents WHERE catalog_generation<>?",
            (catalog_generation,),
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('catalog_generation',?)",
            (catalog_generation,),
        )
        self.connection.commit()
        return stale

    def acquire_next(
        self,
        maximum_attempts: int = 3,
        maximum_priority: int | None = None,
    ) -> ContentTask | None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            priority_clause = " AND priority<=?" if maximum_priority is not None else ""
            parameters = (
                (maximum_attempts, maximum_priority)
                if maximum_priority is not None else (maximum_attempts,)
            )
            row = self.connection.execute(
                """
                SELECT document_key, path, source_version, partition_year,
                       priority, attempts
                FROM documents
                WHERE status='pending' AND attempts<?
                """ + priority_clause + """
                ORDER BY priority, COALESCE(partition_year, 0) DESC, path COLLATE NOCASE
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            self.connection.execute(
                "UPDATE documents SET status='processing', attempts=attempts+1, "
                "lease_until=datetime('now','+10 minutes'), updated_at=? "
                "WHERE document_key=?",
                (utc_now(), row["document_key"]),
            )
            self.connection.commit()
            return ContentTask(
                document_key=row["document_key"],
                path=row["path"],
                source_version=row["source_version"],
                partition_year=row["partition_year"],
                priority=row["priority"],
                attempts=row["attempts"] + 1,
            )
        except Exception:
            self.connection.rollback()
            raise

    def recover_expired_leases(self):
        self.connection.execute(
            "UPDATE documents SET status='pending', lease_until='', updated_at=? "
            "WHERE status='processing' AND lease_until<datetime('now')",
            (utc_now(),),
        )
        self.connection.commit()

    def complete(
        self,
        task: ContentTask,
        *,
        shard_name: str,
        content_status: str,
        extracted_characters: int,
        content_error: str = "",
    ):
        self.connection.execute(
            """
            UPDATE documents
            SET status='completed', shard_name=?, content_status=?, content_error=?,
                extracted_characters=?, lease_until='', updated_at=?
            WHERE document_key=? AND source_version=?
            """,
            (
                shard_name, content_status, content_error[:2000],
                extracted_characters, utc_now(),
                task.document_key, task.source_version,
            ),
        )
        self.connection.commit()

    def fail(self, task: ContentTask, error: str, maximum_attempts: int = 3):
        status = "failed" if task.attempts >= maximum_attempts else "pending"
        self.connection.execute(
            "UPDATE documents SET status=?, content_status='error', content_error=?, "
            "lease_until='', updated_at=? WHERE document_key=? AND source_version=?",
            (status, error[:2000], utc_now(), task.document_key, task.source_version),
        )
        self.connection.commit()

    def progress(self) -> ContentProgress:
        row = self.connection.execute(
            """
            SELECT COUNT(*) total,
                   SUM(status='completed') completed,
                   SUM(status IN ('pending','processing')) pending,
                   SUM(status='failed') failed,
                   COALESCE(SUM(source_size),0) total_bytes,
                   COALESCE(SUM(CASE WHEN status='completed' THEN source_size ELSE 0 END),0)
                       completed_bytes
            FROM documents
            """
        ).fetchone()
        return ContentProgress(
            int(row["total"] or 0), int(row["completed"] or 0),
            int(row["pending"] or 0), int(row["failed"] or 0),
            int(row["total_bytes"] or 0), int(row["completed_bytes"] or 0),
        )

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


class ContentShard:
    """Own one bounded SQLite FTS shard and its compressed source text."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.executescript(
                """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                document_key TEXT UNIQUE NOT NULL,
                source_version TEXT NOT NULL,
                path TEXT NOT NULL,
                compressed_content BLOB NOT NULL,
                character_count INTEGER NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
                content,
                content='',
                tokenize='unicode61 remove_diacritics 2'
            );
                """
            )
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def upsert(self, task: ContentTask, content: str):
        previous = self.connection.execute(
            "SELECT id,compressed_content FROM documents WHERE document_key=?",
            (task.document_key,),
        ).fetchone()
        if previous is not None:
            self.connection.execute(
                "INSERT INTO document_fts(document_fts,rowid,content) "
                "VALUES('delete',?,?)",
                (
                    previous["id"],
                    self._decompress(previous["compressed_content"]),
                ),
            )
            self.connection.execute(
                "DELETE FROM documents WHERE id=?", (previous["id"],)
            )
        cursor = self.connection.execute(
            """
            INSERT INTO documents(
                document_key, source_version, path, compressed_content,
                character_count, indexed_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                task.document_key, task.source_version, task.path,
                sqlite3.Binary(zlib.compress(content.encode("utf-8"), level=6)),
                len(content), utc_now(),
            ),
        )
        if content.strip():
            self.connection.execute(
                "INSERT INTO document_fts(rowid, content) VALUES(?, ?)",
                (cursor.lastrowid, content),
            )
        self.connection.commit()

    def delete(self, document_key: str):
        row = self.connection.execute(
            "SELECT id,compressed_content FROM documents WHERE document_key=?",
            (document_key,),
        ).fetchone()
        if row is None:
            return
        self.connection.execute(
            "INSERT INTO document_fts(document_fts,rowid,content) VALUES('delete',?,?)",
            (row["id"], self._decompress(row["compressed_content"])),
        )
        self.connection.execute("DELETE FROM documents WHERE id=?", (row["id"],))
        self.connection.commit()

    def search(self, query: str, limit: int = 200) -> list[ContentSearchHit]:
        terms = TOKEN_RE.findall(query)
        if not terms:
            return []
        fts_query = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms)
        rows = self.connection.execute(
            """
            SELECT documents.*, bm25(document_fts) AS rank
            FROM document_fts
            JOIN documents ON documents.id=document_fts.rowid
            WHERE document_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, max(1, int(limit))),
        ).fetchall()
        return [
            ContentSearchHit(
                document_key=row["document_key"],
                source_version=row["source_version"],
                path=row["path"],
                excerpt=self._excerpt(self._decompress(row["compressed_content"]), terms),
                rank=float(row["rank"]),
                shard_name=self.path.name,
            )
            for row in rows
        ]

    def get_text(self, document_key: str) -> str:
        row = self.connection.execute(
            "SELECT compressed_content FROM documents WHERE document_key=?",
            (document_key,),
        ).fetchone()
        return self._decompress(row[0]) if row is not None else ""

    @staticmethod
    def _decompress(value: bytes) -> str:
        return zlib.decompress(value).decode("utf-8", errors="replace")

    @staticmethod
    def _excerpt(content: str, terms: list[str], radius: int = 100) -> str:
        lowered = content.casefold()
        positions = [lowered.find(term.casefold()) for term in terms]
        position = min((value for value in positions if value >= 0), default=0)
        start = max(0, position - radius)
        end = min(len(content), position + radius)
        excerpt = re.sub(r"\s+", " ", content[start:end]).strip()
        return ("… " if start else "") + excerpt + (" …" if end < len(content) else "")

    def allocated_size(self) -> int:
        self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        page_size = int(self.connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(self.connection.execute("PRAGMA page_count").fetchone()[0])
        return page_size * page_count

    def integrity_check(self) -> bool:
        return self.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def optimize(self):
        self.connection.execute("INSERT INTO document_fts(document_fts) VALUES('optimize')")
        self.connection.execute("PRAGMA optimize")
        self.connection.commit()

    def free_page_ratio(self) -> float:
        pages = int(self.connection.execute("PRAGMA page_count").fetchone()[0])
        free = int(self.connection.execute("PRAGMA freelist_count").fetchone()[0])
        return free / pages if pages else 0.0

    def compact(self):
        self.connection.execute("VACUUM")
        self.connection.execute("PRAGMA optimize")

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


class ShardRepository:
    """Allocate and query year-based shards with bounded physical size."""

    def __init__(
        self,
        layout: IndexLayout,
        state: ContentStateRepository,
        target_bytes: int = DEFAULT_SHARD_TARGET_BYTES,
    ):
        self.layout = layout
        self.state = state
        self.target_bytes = max(4096, int(target_bytes))

    @staticmethod
    def partition_label(year: int | None) -> str:
        return str(year) if year is not None else "unassigned"

    def writable_shard(self, year: int | None) -> str:
        row = self.state.connection.execute(
            "SELECT * FROM shards WHERE partition_year IS ? AND status='open' "
            "ORDER BY sequence DESC LIMIT 1",
            (year,),
        ).fetchone()
        if row is not None:
            path = self.layout.shard_path(row["name"])
            with ContentShard(path) as shard:
                size = shard.allocated_size()
            if size < self.target_bytes:
                return str(row["name"])
            self.state.connection.execute(
                "UPDATE shards SET status='sealed', size_bytes=?, updated_at=? WHERE name=?",
                (size, utc_now(), row["name"]),
            )
            sequence = int(row["sequence"]) + 1
        else:
            previous = self.state.connection.execute(
                "SELECT MAX(sequence) FROM shards WHERE partition_year IS ?", (year,)
            ).fetchone()[0]
            sequence = int(previous or 0) + 1
        name = f"{self.partition_label(year)}-{sequence:03d}.db"
        self.state.connection.execute(
            "INSERT INTO shards(name,partition_year,sequence,status,updated_at) "
            "VALUES(?,?,?,'open',?)",
            (name, year, sequence, utc_now()),
        )
        self.state.connection.commit()
        return name

    def store(self, task: ContentTask, content: str) -> str:
        current = self.state.connection.execute(
            "SELECT shard_name FROM documents WHERE document_key=?",
            (task.document_key,),
        ).fetchone()
        name = str(current["shard_name"] or "") if current is not None else ""
        if not name:
            name = self.writable_shard(task.partition_year)
        with ContentShard(self.layout.shard_path(name)) as shard:
            shard.upsert(task, content)
            size = shard.allocated_size()
            document_count = int(
                shard.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )
        self.state.connection.execute(
            "UPDATE shards SET size_bytes=?, document_count=?, updated_at=? "
            "WHERE name=?",
            (size, document_count, utc_now(), name),
        )
        self.state.connection.commit()
        return name

    def delete(self, document_key: str, shard_name: str):
        if not shard_name:
            return
        path = self.layout.shard_path(shard_name)
        if path.exists():
            with ContentShard(path) as shard:
                shard.delete(document_key)
                size = shard.allocated_size()
                document_count = int(
                    shard.connection.execute(
                        "SELECT COUNT(*) FROM documents"
                    ).fetchone()[0]
                )
            self.state.connection.execute(
                "UPDATE shards SET size_bytes=?,document_count=?,updated_at=? "
                "WHERE name=?",
                (size, document_count, utc_now(), shard_name),
            )
            self.state.connection.commit()

    def shard_names(self, years: set[int] | None = None) -> list[str]:
        if years:
            placeholders = ",".join("?" for _ in years)
            rows = self.state.connection.execute(
                f"SELECT name FROM shards WHERE partition_year IN ({placeholders}) ORDER BY partition_year DESC, sequence",
                tuple(sorted(years)),
            )
        else:
            rows = self.state.connection.execute(
                "SELECT name FROM shards ORDER BY partition_year DESC, sequence"
            )
        return [str(row[0]) for row in rows]


class ContentDocumentRepository:
    """Read source-bounded extracted documents for customer enrichment."""

    def __init__(self, layout: IndexLayout, catalog_path: Path):
        self.layout = layout
        self.catalog_path = catalog_path

    def documents_for_folder(
        self,
        folder_path: str,
        max_documents: int = 24,
        max_characters_per_document: int = 80_000,
    ) -> list[dict]:
        if not self.layout.content_state_path.exists() or not self.catalog_path.exists():
            return []
        folder = str(Path(folder_path).resolve())
        catalog = sqlite3.connect(f"file:{self.catalog_path}?mode=ro", uri=True)
        catalog.row_factory = sqlite3.Row
        state = sqlite3.connect(
            f"file:{self.layout.content_state_path}?mode=ro", uri=True
        )
        state.row_factory = sqlite3.Row
        try:
            rows = catalog.execute(
                """
                SELECT document_key,path,filename,file_type,source_version
                FROM files
                WHERE project_root_path=? OR folder_path=? OR path LIKE ?
                ORDER BY modified_date DESC,path COLLATE NOCASE LIMIT ?
                """,
                (folder, folder, f"{folder}{os.sep}%", max(1, int(max_documents))),
            ).fetchall()
            mappings = {}
            keys = [str(row["document_key"]) for row in rows]
            if keys:
                placeholders = ",".join("?" for _ in keys)
                mappings = {
                    str(row["document_key"]): row
                    for row in state.execute(
                        f"SELECT document_key,source_version,shard_name FROM documents "
                        f"WHERE status='completed' AND document_key IN ({placeholders})",
                        keys,
                    )
                }
            result = []
            open_shards: dict[str, ContentShard] = {}
            try:
                for row in rows:
                    mapping = mappings.get(str(row["document_key"]))
                    if (
                        mapping is None
                        or mapping["source_version"] != row["source_version"]
                        or not mapping["shard_name"]
                    ):
                        continue
                    name = str(mapping["shard_name"])
                    shard = open_shards.get(name)
                    if shard is None:
                        shard = ContentShard(self.layout.shard_path(name))
                        open_shards[name] = shard
                    text = shard.get_text(str(row["document_key"]))
                    if text.strip():
                        result.append({
                            "path": str(row["path"]),
                            "filename": str(row["filename"]),
                            "file_type": str(row["file_type"] or "").casefold(),
                            "content": text[:max_characters_per_document],
                        })
            finally:
                for shard in open_shards.values():
                    shard.close()
            return result
        finally:
            state.close()
            catalog.close()

    def has_pending_documents(self, folder_path: str) -> bool:
        if not self.layout.content_state_path.exists() or not self.catalog_path.exists():
            return False
        folder = str(Path(folder_path).resolve())
        catalog = sqlite3.connect(f"file:{self.catalog_path}?mode=ro", uri=True)
        state = sqlite3.connect(
            f"file:{self.layout.content_state_path}?mode=ro", uri=True
        )
        try:
            keys = [
                str(row[0])
                for row in catalog.execute(
                    "SELECT document_key FROM files WHERE content_eligible=1 AND "
                    "(project_root_path=? OR folder_path=? OR path LIKE ?)",
                    (folder, folder, f"{folder}{os.sep}%"),
                )
            ]
            if not keys:
                return False
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                if state.execute(
                    f"SELECT 1 FROM documents WHERE status IN ('pending','processing') "
                    f"AND document_key IN ({placeholders}) LIMIT 1",
                    chunk,
                ).fetchone() is not None:
                    return True
            return False
        finally:
            state.close()
            catalog.close()


class ShardMaintenanceService:
    """Keep derived shards healthy without risking catalog or customer data."""

    def __init__(self, layout: IndexLayout, state: ContentStateRepository):
        self.layout = layout
        self.state = state

    def maintain(self, fragmentation_threshold: float = 0.25) -> dict[str, int]:
        result = {"optimized": 0, "compacted": 0, "requeued": 0}
        rows = self.state.connection.execute("SELECT name FROM shards").fetchall()
        for row in rows:
            name = str(row["name"])
            path = self.layout.shard_path(name)
            if not path.exists():
                result["requeued"] += self._requeue(name)
                continue
            try:
                with ContentShard(path) as shard:
                    if not shard.integrity_check():
                        raise sqlite3.DatabaseError("Integritätsprüfung fehlgeschlagen")
                    if shard.free_page_ratio() >= fragmentation_threshold:
                        shard.compact()
                        result["compacted"] += 1
                    else:
                        shard.optimize()
                        result["optimized"] += 1
            except (OSError, sqlite3.Error, zlib.error):
                self.layout.corrupt_shard_dir.mkdir(parents=True, exist_ok=True)
                destination = self.layout.corrupt_shard_dir / name
                if destination.exists():
                    destination = self.layout.corrupt_shard_dir / (
                        f"{path.stem}-{int(datetime.now().timestamp())}.db"
                    )
                if path.exists():
                    shutil.move(path, destination)
                result["requeued"] += self._requeue(name)
        self.state.connection.commit()
        return result

    def _requeue(self, shard_name: str) -> int:
        cursor = self.state.connection.execute(
            "UPDATE documents SET status='pending',attempts=0,shard_name='',"
            "content_status='',content_error='',updated_at=? WHERE shard_name=?",
            (utc_now(), shard_name),
        )
        self.state.connection.execute("DELETE FROM shards WHERE name=?", (shard_name,))
        return max(cursor.rowcount, 0)
