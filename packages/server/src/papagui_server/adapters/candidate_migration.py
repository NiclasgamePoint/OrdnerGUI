"""Canonicalize legacy suggestions while retaining every source and decision."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from papagui_server.domain.recognition_values import normalize_candidate_value


def canonical_value(kind: str, value: str, payload: dict | None = None) -> str:
    if kind in {"address", "contact"} and payload:
        return json.dumps({
            key: normalize_candidate_value(key, str(item)) or str(item).strip().casefold()
            for key, item in sorted(payload.items()) if item and key not in {"id", "address_kind"}
        }, ensure_ascii=False, sort_keys=True)
    return normalize_candidate_value(kind, value) or "raw:" + value.strip().casefold()


def candidate_key(customer_id: int, kind: str, normalized: str, party_key: str) -> str:
    return hashlib.sha256(json.dumps(
        [customer_id, kind, normalized, party_key], ensure_ascii=False
    ).encode()).hexdigest()


def migrate_candidates(connection: sqlite3.Connection) -> None:
    _migrate_evidence_assessments(connection)
    if connection.execute(
        "SELECT 1 FROM candidate_schema_metadata WHERE key='canonical_version' AND value='1'"
    ).fetchone():
        return
    # Decided records take precedence, so migration never reopens an old decision.
    rows = connection.execute(
        "SELECT * FROM customer_document_suggestions ORDER BY "
        "CASE status WHEN 'accepted' THEN 0 WHEN 'rejected' THEN 1 ELSE 2 END,id"
    ).fetchall()
    groups: dict[str, int] = {}
    for row in rows:
        kind = str(row["kind"])
        payload = json.loads(row["payload_json"])
        if str(row["suggestion_type"]) == "contact":
            kind = "contact"
            payload = {key: str(row[f"contact_{key}"]) for key in ("name", "role", "email", "phone")}
        normalized = canonical_value(kind, str(row["value"]), payload)
        key = candidate_key(int(row["customer_id"]), kind, normalized, "customer")
        winner = groups.setdefault(key, int(row["id"]))
        invalid = kind in {"phone", "email"} and normalized.startswith("raw:")
        lifecycle = "superseded" if winner != row["id"] else (
            "invalid" if invalid and row["status"] == "pending" else "active"
        )
        connection.execute(
            "UPDATE customer_document_suggestions SET kind=?,normalized_value=?,"
            "payload_json=?,canonical_id=?,lifecycle=? WHERE id=?",
            (kind, normalized, json.dumps(payload), None if winner == row["id"] else winner,
             lifecycle, row["id"]),
        )
        for alias in (str(row["fingerprint"]), key):
            connection.execute(
                "INSERT OR IGNORE INTO candidate_aliases VALUES (?,?)", (alias, winner)
            )
        connection.execute(
            "INSERT OR IGNORE INTO candidate_evidence(candidate_id,source_path,excerpt) "
            "VALUES (?,?,?)", (winner, row["source_path"], row["excerpt"])
        )
        if row["status"] in {"accepted", "rejected"}:
            other = connection.execute(
                "SELECT 1 FROM candidate_decisions WHERE candidate_id=? AND action<>?",
                (winner, row["status"]),
            ).fetchone()
            if other:
                connection.execute(
                    "UPDATE customer_document_suggestions SET quality='review',"
                    "reasons_json='[\"legacy_decision_conflict\"]' WHERE id=?", (winner,)
                )
            connection.execute(
                """INSERT OR IGNORE INTO candidate_decisions(candidate_id,
                    original_suggestion_id,action,reason,decided_at) VALUES (?,?,?,?,?)""",
                (winner, row["id"], row["status"], "legacy_decision", row["resolved_at"] or row["created_at"]),
            )
    connection.execute(
        "INSERT OR REPLACE INTO candidate_schema_metadata VALUES ('canonical_version','1')"
    )


def _migrate_evidence_assessments(connection: sqlite3.Connection) -> None:
    """Older aggregate quality is not proof of the quality of each old source."""
    if connection.execute(
        "SELECT 1 FROM candidate_schema_metadata WHERE key='evidence_assessment_version' AND value='2'"
    ).fetchone():
        return
    columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_evidence)")}
    for name, definition in {
        "quality": "TEXT NOT NULL DEFAULT 'legacy'",
        "party_role": "TEXT NOT NULL DEFAULT 'unknown'",
        "reasons_json": "TEXT NOT NULL DEFAULT '[]'",
        "engine_version": "TEXT NOT NULL DEFAULT ''",
        "document_family": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE candidate_evidence ADD COLUMN {name} {definition}")
    connection.execute(
        "INSERT OR REPLACE INTO candidate_schema_metadata VALUES ('evidence_assessment_version','2')"
    )
