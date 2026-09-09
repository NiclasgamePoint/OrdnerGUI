"""Idempotent SQLite schema and migrations for server-owned customer data."""

from __future__ import annotations

import json
import sqlite3

from papagui_server.adapters.candidate_schema import initialize_candidate_schema

from papagui_server.domain.source_paths import coerce_source_path, source_uri


def initialize_customer_schema(connection: sqlite3.Connection, *, force: bool = False) -> None:
    """Create the current schema inside a recoverable explicit transaction."""
    # Every document result opens a short UoW. Do not rescan legacy customers,
    # paths and provenance for every read or evidence insertion after migration.
    metadata_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_schema_metadata'"
    ).fetchone()
    if not force and metadata_exists and connection.execute(
        "SELECT 1 FROM candidate_schema_metadata WHERE key='storage_layout_version' AND value='4'"
    ).fetchone():
        return
    try:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY,
                folder_path TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'Unternehmen',
                company TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                street TEXT NOT NULL DEFAULT '',
                postal_code TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS customer_services (
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                name TEXT NOT NULL, PRIMARY KEY(customer_id, name)
            );
            CREATE TABLE IF NOT EXISTS customer_folders (
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                folder_path TEXT UNIQUE NOT NULL, PRIMARY KEY(customer_id, folder_path)
            );
            CREATE TABLE IF NOT EXISTS service_types (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                normalized_name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_projects (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                project_root_id INTEGER,
                source_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                service_type TEXT NOT NULL DEFAULT '',
                service_type_id INTEGER NOT NULL DEFAULT 0,
                folder_path TEXT NOT NULL DEFAULT '',
                folder_key TEXT UNIQUE NOT NULL DEFAULT '',
                project_label TEXT NOT NULL DEFAULT '',
                project_city TEXT NOT NULL DEFAULT '',
                year INTEGER,
                source TEXT NOT NULL DEFAULT 'folder',
                provenance TEXT NOT NULL DEFAULT 'folder',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_id, relative_path)
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                body TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customer_tags (
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY(customer_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS customer_journal_entries (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                entry_number INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_document_suggestions (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                source_path TEXT NOT NULL,
                excerpt TEXT NOT NULL DEFAULT '',
                fingerprint TEXT UNIQUE NOT NULL,
                confidence REAL NOT NULL,
                rule TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                suggestion_type TEXT NOT NULL DEFAULT 'field',
                contact_name TEXT NOT NULL DEFAULT '',
                contact_role TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                contact_phone TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS recognition_runs (
                id INTEGER PRIMARY KEY,
                detected INTEGER NOT NULL DEFAULT 0,
                created INTEGER NOT NULL DEFAULT 0,
                assigned INTEGER NOT NULL DEFAULT 0,
                pending INTEGER NOT NULL DEFAULT 0,
                rejected INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS recognition_run_history (
                id INTEGER PRIMARY KEY,
                detected INTEGER NOT NULL DEFAULT 0,
                created INTEGER NOT NULL DEFAULT 0,
                assigned INTEGER NOT NULL DEFAULT 0,
                pending INTEGER NOT NULL DEFAULT 0,
                rejected INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS recognition_cases (
                signature TEXT PRIMARY KEY,
                recognition_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                project_roots_json TEXT NOT NULL,
                cities_json TEXT NOT NULL DEFAULT '[]',
                service_types_json TEXT NOT NULL DEFAULT '[]',
                years_json TEXT NOT NULL DEFAULT '[]',
                reason TEXT NOT NULL DEFAULT '',
                suggested_customer_ids_json TEXT NOT NULL DEFAULT '[]',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                last_seen_run_id INTEGER REFERENCES recognition_run_history(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS recognition_decisions (
                signature TEXT PRIMARY KEY REFERENCES recognition_cases(signature)
                    ON DELETE CASCADE,
                action TEXT NOT NULL,
                customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
                decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS api_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                operation TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_customers_display_name
                ON customers(display_name COLLATE NOCASE);
            """
        )
        _ensure_columns(
            connection,
            "customers",
            {"revision": "INTEGER NOT NULL DEFAULT 1"},
        )
        _ensure_columns(
            connection,
            "customer_document_suggestions",
            {
                "rule": "TEXT NOT NULL DEFAULT ''",
                "resolved_at": "TEXT NOT NULL DEFAULT ''",
                "suggestion_type": "TEXT NOT NULL DEFAULT 'field'",
                "contact_name": "TEXT NOT NULL DEFAULT ''",
                "contact_role": "TEXT NOT NULL DEFAULT ''",
                "contact_email": "TEXT NOT NULL DEFAULT ''",
                "contact_phone": "TEXT NOT NULL DEFAULT ''",
            },
        )
        _migrate_customer_projects(connection)
        _migrate_recognition_cases(connection)
        _migrate_recognition_run(connection)
        _normalize_legacy_paths(connection)
        _migrate_legacy_suggestions(connection)
        initialize_candidate_schema(connection, _ensure_columns)
        # Keep the schema upgrade inside the explicit transaction opened above.
        # ``sqlite3.Connection.executescript`` would implicitly commit pending
        # statements before running a second script.
        for statement in (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_projects_source "
            "ON customer_projects(source_id, relative_path)",
            "CREATE INDEX IF NOT EXISTS idx_customer_projects_customer "
            "ON customer_projects(customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_customer_projects_root "
            "ON customer_projects(project_root_id)",
            "CREATE INDEX IF NOT EXISTS idx_suggestions_customer_status "
            "ON customer_document_suggestions(customer_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_recognition_cases_status "
            "ON recognition_cases(status, recognition_key)",
            "CREATE INDEX IF NOT EXISTS idx_recognition_runs_started "
            "ON recognition_run_history(started_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_api_idempotency_created_at "
            "ON api_idempotency(created_at)",
        ):
            connection.execute(statement)
        connection.execute(
            "INSERT OR REPLACE INTO candidate_schema_metadata VALUES ('storage_layout_version','4')"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _ensure_columns(
    connection: sqlite3.Connection, table: str, definitions: dict[str, str]
) -> None:
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    for name, definition in definitions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_customer_projects(connection: sqlite3.Connection) -> None:
    _ensure_columns(
        connection,
        "customer_projects",
        {
            "project_root_id": "INTEGER",
            "source_id": "TEXT NOT NULL DEFAULT 'primary'",
            "relative_path": "TEXT NOT NULL DEFAULT ''",
            "service_type": "TEXT NOT NULL DEFAULT ''",
            "service_type_id": "INTEGER NOT NULL DEFAULT 0",
            "folder_path": "TEXT NOT NULL DEFAULT ''",
            "folder_key": "TEXT NOT NULL DEFAULT ''",
            "source": "TEXT NOT NULL DEFAULT 'folder'",
            "provenance": "TEXT NOT NULL DEFAULT 'folder'",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        },
    )
    rows = connection.execute(
        "SELECT id, folder_path, source_id, relative_path, service_type, "
        "service_type_id, source, provenance FROM customer_projects"
    ).fetchall()
    for row in rows:
        source_id, relative = _portable_legacy_path(str(row[1]))
        service_type = str(row[4])
        if not service_type and int(row[5] or 0):
            service = connection.execute(
                "SELECT name FROM service_types WHERE id=?", (int(row[5]),)
            ).fetchone()
            service_type = str(service[0]) if service is not None else ""
        connection.execute(
            "UPDATE customer_projects SET source_id=?, relative_path=?, "
            "service_type=?, provenance=? WHERE id=?",
            (
                (str(row[2]) if str(row[3]) else source_id),
                str(row[3]) or relative,
                service_type,
                str(row[7] or row[6] or "folder"),
                int(row[0]),
            ),
        )


def _migrate_recognition_cases(connection: sqlite3.Connection) -> None:
    _ensure_columns(
        connection,
        "recognition_cases",
        {
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "payload_json": "TEXT NOT NULL DEFAULT '{}'",
            "project_roots_json": "TEXT NOT NULL DEFAULT '[]'",
            "cities_json": "TEXT NOT NULL DEFAULT '[]'",
            "service_types_json": "TEXT NOT NULL DEFAULT '[]'",
            "years_json": "TEXT NOT NULL DEFAULT '[]'",
            "suggested_customer_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            "last_seen_run_id": "INTEGER",
        },
    )
    rows = connection.execute(
        "SELECT signature, payload_json, display_name, project_roots_json "
        "FROM recognition_cases"
    ).fetchall()
    for signature, raw_payload, display_name, raw_roots in rows:
        try:
            payload = json.loads(str(raw_payload or "{}"))
        except (TypeError, ValueError):
            payload = {}
        name = str(display_name or payload.get("display_name") or "Unbekannt")
        try:
            roots = json.loads(str(raw_roots or "[]"))
        except (TypeError, ValueError):
            roots = []
        if not roots:
            roots = [{"source_id": "legacy", "relative_path": f"cases/{signature}"}]
        connection.execute(
            "UPDATE recognition_cases SET display_name=?, project_roots_json=? "
            "WHERE signature=?",
            (name, json.dumps(roots, ensure_ascii=False), str(signature)),
        )


def _migrate_recognition_run(connection: sqlite3.Connection) -> None:
    names = [
        str(item[1]) for item in connection.execute("PRAGMA table_info(recognition_runs)")
    ]
    if not names:
        return
    row = connection.execute(
        "SELECT * FROM recognition_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return
    values = dict(zip(names, row))
    connection.execute(
        """
        INSERT OR IGNORE INTO recognition_run_history(
            id, detected, created, assigned, pending, rejected, error,
            started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(values.get("id") or 1),
            int(values.get("detected") or 0),
            int(values.get("created") or 0),
            int(values.get("assigned") or 0),
            int(values.get("pending") or 0),
            int(values.get("rejected") or 0),
            str(values.get("error") or ""),
            str(values.get("started_at") or values.get("finished_at") or ""),
            str(values.get("finished_at") or ""),
        ),
    )


def _portable_legacy_path(value: str) -> tuple[str, str]:
    source = coerce_source_path(value)
    return source.source_id, source.relative_path


def _normalize_legacy_paths(connection: sqlite3.Connection) -> None:
    customers = connection.execute("SELECT id, folder_path FROM customers").fetchall()
    for customer_id, value in customers:
        normalized = _normalized_reference(str(value), customer_id=int(customer_id))
        try:
            connection.execute(
                "UPDATE customers SET folder_path=? WHERE id=?",
                (normalized, int(customer_id)),
            )
        except sqlite3.IntegrityError:
            connection.execute(
                "UPDATE customers SET folder_path=? WHERE id=?",
                (f"customer://migration-{int(customer_id)}", int(customer_id)),
            )
    folders = connection.execute(
        "SELECT customer_id, folder_path FROM customer_folders"
    ).fetchall()
    connection.execute("DELETE FROM customer_folders")
    connection.executemany(
        "INSERT OR IGNORE INTO customer_folders(customer_id, folder_path) VALUES (?, ?)",
        [
            (int(customer_id), _normalized_reference(str(value)))
            for customer_id, value in folders
        ],
    )
    projects = connection.execute(
        "SELECT id, source_id, relative_path, folder_path FROM customer_projects"
    ).fetchall()
    for project_id, source_id, relative_path, folder_path in projects:
        source = (
            coerce_source_path(str(folder_path))
            if not str(relative_path)
            else coerce_source_path(
                f"source://{str(source_id)}/{str(relative_path).lstrip('/')}"
            )
        )
        uri = source_uri(source)
        connection.execute(
            "UPDATE customer_projects SET source_id=?, relative_path=?, "
            "folder_path=?, folder_key=? WHERE id=?",
            (source.source_id, source.relative_path, uri, uri.casefold(), int(project_id)),
        )
    for table in ("customer_document_suggestions", "customer_data_suggestions"):
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            continue
        rows = connection.execute(f"SELECT id, source_path FROM {table}").fetchall()
        for item_id, value in rows:
            if str(value).strip():
                connection.execute(
                    f"UPDATE {table} SET source_path=? WHERE id=?",
                    (_normalized_reference(str(value)), int(item_id)),
                )


def _normalized_reference(value: str, *, customer_id: int | None = None) -> str:
    if value.startswith("customer://"):
        return value
    if not value.strip():
        return f"customer://migration-{customer_id}" if customer_id is not None else ""
    return source_uri(coerce_source_path(value))


def _migrate_legacy_suggestions(connection: sqlite3.Connection) -> None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='customer_data_suggestions'"
    ).fetchone()
    if exists is None:
        return
    rows = connection.execute(
        """
        SELECT customer_id, field_name, suggested_value, source_path, excerpt,
               rule, confidence, status, suggestion_type, contact_name,
               contact_role, contact_email, contact_phone, fingerprint, created_at
          FROM customer_data_suggestions
        """
    ).fetchall()
    editable = {
        "company", "email", "phone", "street", "postal_code", "city", "entity_type"
    }
    for row in rows:
        suggestion_type = str(row[8] or "field").casefold()
        is_contact = suggestion_type == "contact" or any(
            str(row[index] or "").strip() for index in range(9, 13)
        )
        kind = "contact" if is_contact else str(row[1]).strip()
        value = (
            str(row[9] or row[11] or row[12] or "Kontakt").strip()
            if is_contact
            else str(row[2] or "").strip()
        )
        if (not is_contact and kind not in editable) or not value:
            continue
        status = str(row[7] or "pending").casefold()
        if status not in {"pending", "accepted", "rejected"}:
            status = "rejected" if status in {"ignored", "declined"} else "pending"
        fingerprint = str(row[13] or "").strip() or (
            f"legacy-{int(row[0])}-{kind}-{value}"
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO customer_document_suggestions(
                customer_id, kind, value, source_path, excerpt, fingerprint,
                confidence, rule, status, suggestion_type, contact_name,
                contact_role, contact_email, contact_phone, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row[0]), kind, value, str(row[3]), str(row[4]), fingerprint,
                float(row[6] or 0), str(row[5]), status,
                "contact" if is_contact else "field",
                str(row[9] or ""), str(row[10] or ""), str(row[11] or ""),
                str(row[12] or ""), str(row[14] or ""),
            ),
        )
