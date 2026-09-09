"""Revalidate derived suggestions without changing customer values or decisions."""
from __future__ import annotations

import json

from papagui_server.domain.recognition_values import normalize_candidate_value, valid_contact_name


def valid_suggestion(kind, value, payload=None):
    if kind in {"phone", "email"}:
        return bool(normalize_candidate_value(kind, value))
    if kind == "contact":
        contact = payload or {}
        return valid_contact_name(contact.get("name", "")) and all(
            not contact.get(field) or normalize_candidate_value(field, contact[field])
            for field in ("phone", "email")
        )
    return True


def revalidate_pending_candidates(connection):
    """A versioned, one-time cleanup also covers databases already canonicalized."""
    if connection.execute(
        "SELECT 1 FROM candidate_schema_metadata WHERE key='candidate_validation_version' AND value='1'"
    ).fetchone():
        return
    for row in connection.execute(
        "SELECT * FROM customer_document_suggestions WHERE status='pending' "
        "AND canonical_id IS NULL AND lifecycle<>'invalid'"
    ).fetchall():
        kind = "contact" if row["suggestion_type"] == "contact" else row["kind"]
        payload = json.loads(row["payload_json"]) or {
            key: row[f"contact_{key}"] for key in ("name", "role", "email", "phone")
        }
        if not valid_suggestion(kind, row["value"], payload):
            connection.execute(
                "UPDATE customer_document_suggestions SET lifecycle='invalid' WHERE id=?", (row["id"],)
            )
    connection.execute(
        "INSERT OR REPLACE INTO candidate_schema_metadata VALUES ('candidate_validation_version','1')"
    )
