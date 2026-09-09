#!/usr/bin/env python3
"""Preview recognition schema migration on a temporary SQLite backup only.

An explicit --database is mandatory. The source is opened with SQLite mode=ro;
all schema and canonical migration writes target a temporary backup. Output is
limited to aggregate counts, booleans and whole-dataset checksums. No customer
names, values, source paths, excerpts, or record IDs are emitted, including errors.

This checks migration integrity and preservation. It is not a semantic quality
acceptance test, does not re-read documents, and does not activate a rollout.
Do not run against any customer database without separate explicit authorization.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/server/src", ROOT / "packages/contracts/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from papagui_server.adapters.candidate_migration import migrate_candidates  # noqa: E402
from papagui_server.adapters.customer_schema import initialize_customer_schema  # noqa: E402


class PreviewError(Exception):
    """A fixed, non-identifying error code; original exception text stays private."""


_PROTECTED = {
    "customer_values": ("customers",),
    "contact_values": ("contacts",),
    "project_values": ("customer_projects", "customer_services", "customer_folders"),
    "other_customer_values": ("notes", "tags", "customer_tags", "customer_journal_entries"),
    "decisions": ("recognition_decisions", "candidate_decisions", "customer_document_suggestions", "customer_data_suggestions"),
}
_DECISION_COLUMNS = frozenset({"id", "customer_id", "status", "value", "suggested_value", "contact_name", "contact_role", "contact_email", "contact_phone", "created_at", "resolved_at"})


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _digest(value: Any) -> str:
    def default(item: Any):
        if isinstance(item, bytes):
            return {"blob_hex": item.hex()}
        raise TypeError("unsupported_sqlite_value")
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=default).encode()).hexdigest()


def _file_checksum(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def _source_checksum(path: Path) -> str:
    # The WAL contains committed user data; SHM is SQLite's lock/read-index state.
    files = [path, Path(str(path) + "-wal"), Path(str(path) + "-journal")]
    return _digest([_file_checksum(item) if item.exists() else None for item in files])


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]


def _projection(connection: sqlite3.Connection, table: str, columns: tuple[str, ...], *, decided_only: bool) -> Counter[str]:
    available = set(_columns(connection, table))
    if not available or any(column not in available for column in columns):
        return Counter()
    query = "SELECT " + ",".join(_quote(column) for column in columns) + " FROM " + _quote(table)
    if decided_only:
        query += " WHERE lower(status) IN ('accepted','rejected','ignored','declined')"
    result: Counter[str] = Counter()
    cursor = connection.execute(query)
    while rows := cursor.fetchmany(128):
        result.update(_digest(tuple(row)) for row in rows)
    return result


def _capture(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    snapshots = {}
    for group, tables in _PROTECTED.items():
        for table in tables:
            columns = _columns(connection, table)
            if not columns:
                continue
            decided_only = group == "decisions" and table in {"customer_document_suggestions", "customer_data_suggestions"}
            selected = tuple(column for column in columns if not decided_only or column in _DECISION_COLUMNS)
            snapshots[table] = {"group": group, "columns": selected, "decided_only": decided_only,
                                "rows": _projection(connection, table, selected, decided_only=decided_only)}
    return snapshots


def _compare(connection: sqlite3.Connection, before: dict[str, dict[str, Any]]) -> tuple[dict[str, int], dict[str, str], dict[str, bool]]:
    changed = {group: 0 for group in _PROTECTED}
    before_digests = {group: [] for group in _PROTECTED}
    after_digests = {group: [] for group in _PROTECTED}
    decision_records = 0
    preserved_decisions = 0
    for table, snapshot in before.items():
        group = snapshot["group"]
        old = snapshot["rows"]
        new = _projection(connection, table, snapshot["columns"], decided_only=snapshot["decided_only"])
        removed = sum((old - new).values())
        added = sum((new - old).values())
        # Added canonical decision rows are expected; old decision rows must survive.
        changed[group] += removed if group == "decisions" else max(removed, added)
        before_digests[group].append((table, sorted(old.items())))
        after_digests[group].append((table, sorted(new.items())))
        if group == "decisions":
            decision_records += sum(old.values())
            preserved_decisions += sum((old & new).values())
    counts = {f"changed_{group}": count for group, count in changed.items()}
    counts.update(decision_records_before=decision_records, decision_records_preserved=preserved_decisions)
    checksums = {f"{group}_{stage}": _digest(values[group]) for group in _PROTECTED
                 for stage, values in (("before", before_digests), ("after", after_digests))}
    checks = {"customer_fields_preserved": changed["customer_values"] == 0,
              "contacts_preserved": changed["contact_values"] == 0,
              "projects_preserved": changed["project_values"] == 0,
              "other_customer_data_preserved": changed["other_customer_values"] == 0,
              "all_existing_decisions_preserved": decision_records == preserved_decisions}
    return counts, checksums, checks


def _count(connection: sqlite3.Connection, table: str, where: str = "1=1") -> int:
    if not _columns(connection, table):
        return 0
    return int(connection.execute("SELECT count(*) FROM " + _quote(table) + " WHERE " + where).fetchone()[0])


def _open_before(connection: sqlite3.Connection) -> int:
    """Do not count archived legacy rows again after they were already imported."""
    total = 0
    known_fingerprints: set[str] = set()
    for table in ("customer_document_suggestions", "customer_data_suggestions"):
        columns = set(_columns(connection, table))
        if not columns:
            continue
        cursor = connection.execute("SELECT * FROM " + _quote(table))
        while rows := cursor.fetchmany(128):
            for row in rows:
                fingerprint = str(row["fingerprint"] or "") if "fingerprint" in columns else ""
                archived_duplicate = table == "customer_data_suggestions" and fingerprint and fingerprint in known_fingerprints
                if table == "customer_document_suggestions" and fingerprint:
                    known_fingerprints.add(fingerprint)
                visible = ("canonical_id" not in columns or row["canonical_id"] is None) and ("lifecycle" not in columns or row["lifecycle"] == "active")
                total += int(not archived_duplicate and visible and str(row["status"]).casefold() == "pending")
    return total


def _integrity(connection: sqlite3.Connection) -> bool:
    return [row[0] for row in connection.execute("PRAGMA integrity_check")] == ["ok"] and not connection.execute("PRAGMA foreign_key_check").fetchone()


def preview_migration(database: Path) -> dict[str, Any]:
    """Return only an anonymous report; migrate no original file or document."""
    database = Path(database).resolve()
    if not database.is_file():
        raise PreviewError("database_unavailable")
    try:
        source_before = _source_checksum(database)
        with tempfile.TemporaryDirectory(prefix="recognition-migration-preview-") as temporary:
            backup_path = Path(temporary) / "backup.sqlite"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as original:
                original.execute("PRAGMA query_only=ON")
                original.execute("PRAGMA trusted_schema=OFF")
                with closing(sqlite3.connect(backup_path)) as copied:
                    original.backup(copied)
            with closing(sqlite3.connect(backup_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                if not _integrity(connection):
                    raise PreviewError("integrity_check_failed")
                original_snapshot = _capture(connection)
                counts = {"customers_total": _count(connection, "customers"),
                          "open_before": _open_before(connection)}
                backup_before = _file_checksum(backup_path)
                initialize_customer_schema(connection, force=True)
                migrate_candidates(connection)
                connection.commit()
                after_integrity = _integrity(connection)
                preserved_counts, checksums, checks = _compare(connection, original_snapshot)
                counts.update(preserved_counts)
                counts.update(
                    open_after=_count(connection, "customer_document_suggestions", "status='pending' AND canonical_id IS NULL AND lifecycle='active'"),
                    invalid=_count(connection, "customer_document_suggestions", "lifecycle='invalid'"),
                    superseded=_count(connection, "customer_document_suggestions", "lifecycle='superseded'"),
                    migration_conflicts=_count(connection, "customer_document_suggestions", "canonical_id IS NULL AND reasons_json LIKE '%legacy_decision_conflict%'"),
                )
                checks.update(integrity_before=True, integrity_after=after_integrity)
                checksums.update(backup_before=backup_before, backup_after=_file_checksum(backup_path))
            source_after = _source_checksum(database)
            checks["source_unchanged"] = source_before == source_after
            checksums.update(source_before=source_before, source_after=source_after)
            return {"schema_version": 1, "counts": counts, "checks": checks, "checksums": checksums,
                    "technical_preservation_passed": all(checks.values()), "semantic_quality_accepted": False,
                    "original_database_modified": source_before != source_after}
    except PreviewError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        raise PreviewError("migration_preview_failed") from None


def _output_overlaps_database(output: Path, database: Path) -> bool:
    protected = [item for base in (database, database.resolve())
                 for item in (base, *(Path(str(base) + suffix) for suffix in ("-wal", "-shm", "-journal")))]
    for item in protected:
        if output.resolve() == item.resolve() or (output.exists() and item.exists() and output.samefile(item)):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.output and _output_overlaps_database(args.output, args.database):
            raise PreviewError("output_overlaps_source")
        report = preview_migration(args.database)
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0 if report["technical_preservation_passed"] else 1
    except (PreviewError, OSError) as error:
        code = str(error) if isinstance(error, PreviewError) else "output_unavailable"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
