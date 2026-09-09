"""Additive recognition storage; never discard historical decisions or values."""

from __future__ import annotations

import sqlite3


def initialize_candidate_schema(connection: sqlite3.Connection, ensure_columns) -> None:
    ensure_columns(connection, "contacts", {"uid": "TEXT NOT NULL DEFAULT ''"})
    connection.execute("UPDATE contacts SET uid=lower(hex(randomblob(16))) WHERE uid=''")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_uid ON contacts(uid)")
    ensure_columns(connection, "customer_document_suggestions", {
        "normalized_value": "TEXT NOT NULL DEFAULT ''",
        "party_key": "TEXT NOT NULL DEFAULT 'customer'",
        "party_role": "TEXT NOT NULL DEFAULT 'unknown'",
        "quality": "TEXT NOT NULL DEFAULT 'legacy'",
        "reasons_json": "TEXT NOT NULL DEFAULT '[]'",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "lifecycle": "TEXT NOT NULL DEFAULT 'active'",
        "canonical_id": "INTEGER",
        "engine_version": "TEXT NOT NULL DEFAULT ''",
        "last_seen_run": "TEXT NOT NULL DEFAULT ''",
    })
    statements = (
        """CREATE TABLE IF NOT EXISTS recognition_blocklist (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN
                ('email','phone','email_domain','contact_name','company')),
            value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(kind, normalized_value))""",
        """CREATE TABLE IF NOT EXISTS candidate_aliases (
            fingerprint TEXT PRIMARY KEY,
            candidate_id INTEGER NOT NULL REFERENCES customer_document_suggestions(id)
                ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS candidate_evidence (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER NOT NULL REFERENCES customer_document_suggestions(id)
                ON DELETE CASCADE,
            source_path TEXT NOT NULL,
            document_hash TEXT NOT NULL DEFAULT '',
            document_family TEXT NOT NULL DEFAULT '',
            locator_json TEXT NOT NULL DEFAULT '{}',
            excerpt TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            last_seen_run TEXT NOT NULL DEFAULT '',
            UNIQUE(candidate_id, source_path, locator_json))""",
        """CREATE TABLE IF NOT EXISTS candidate_decisions (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER NOT NULL REFERENCES customer_document_suggestions(id)
                ON DELETE CASCADE,
            original_suggestion_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT 'customer_value',
            decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(original_suggestion_id, action))""",
        """CREATE TABLE IF NOT EXISTS customer_field_provenance (
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            field_name TEXT NOT NULL,
            target_id TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL,
            candidate_id INTEGER REFERENCES customer_document_suggestions(id)
                ON DELETE SET NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(customer_id, field_name, target_id))""",
        """CREATE TABLE IF NOT EXISTS customer_recognition_status (
            customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
            state TEXT NOT NULL DEFAULT 'not_evaluated',
            reason TEXT NOT NULL DEFAULT '',
            counts_json TEXT NOT NULL DEFAULT '{}',
            last_run_at TEXT NOT NULL DEFAULT '',
            pipeline_version TEXT NOT NULL DEFAULT '',
            catalog_version TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS candidate_schema_metadata (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_candidate_evidence_active "
        "ON candidate_evidence(candidate_id, active)",
        "CREATE INDEX IF NOT EXISTS idx_candidates_visible ON "
        "customer_document_suggestions(customer_id, canonical_id, lifecycle, status)",
    )
    for statement in statements:
        connection.execute(statement)
    for name in ("display_name", "entity_type", "company", "email", "phone", "street", "postal_code", "city"):
        connection.execute(
            "INSERT OR IGNORE INTO customer_field_provenance(customer_id,field_name,origin) "
            f"SELECT id, ?, 'unknown' FROM customers WHERE trim({name})<>''", (name,)
        )


def record_provenance(connection, customer_id, fields, *, origin, candidate_id=None, target_id=""):
    for field_name in fields:
        connection.execute(
            """INSERT INTO customer_field_provenance
                   (customer_id,field_name,target_id,origin,candidate_id)
               VALUES (?,?,?,?,?) ON CONFLICT(customer_id,field_name,target_id)
               DO UPDATE SET origin=excluded.origin,candidate_id=excluded.candidate_id,
                   updated_at=CURRENT_TIMESTAMP""",
            (customer_id, field_name, target_id, origin, candidate_id),
        )
