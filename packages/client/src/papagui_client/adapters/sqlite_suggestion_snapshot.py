"""Compact recognition projection from a read-only customer snapshot."""

from __future__ import annotations

import json
import sqlite3
from urllib.parse import unquote, urlsplit

from papagui_contracts import SourcePath
from papagui_contracts.recognition import CustomerSuggestion

from .http_review import SuggestionPage


def _source(value: str) -> dict:
    parts = urlsplit(value)
    if parts.scheme != "source" or not parts.netloc:
        raise ValueError("snapshot suggestion has no portable source reference")
    return SourcePath(parts.netloc, unquote(parts.path).strip("/")).to_dict()


def read_suggestion_page(
    connection: sqlite3.Connection, customer_id: int, status: str, limit: int, offset: int
) -> SuggestionPage:
    """Read only compact decisions; never touch document extraction artifacts."""
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(customer_document_suggestions)")
    }
    if not columns:
        raise ValueError("Dieser gespeicherte Datenstand enthält keine Vorschlagsprüfung.")
    row = connection.execute("SELECT revision FROM customers WHERE id=?", (customer_id,)).fetchone()
    if row is None:
        raise ValueError("Kunde ist im gespeicherten Datenstand nicht vorhanden.")
    revision = int(row[0])
    where = "customer_id=? AND status=?"
    if "lifecycle" in columns:
        where += " AND lifecycle='active'"
    if "canonical_id" in columns:
        where += " AND canonical_id IS NULL"
    parameters = (customer_id, status)
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM customer_document_suggestions WHERE {where}", parameters
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"SELECT * FROM customer_document_suggestions WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
        (*parameters, limit, offset),
    ).fetchall()
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    suggestions = []
    for row in rows:
        values = dict(row)
        values["field_name"] = values["kind"]
        values["source"] = _source(values["source_path"])
        if values.get("suggestion_type") == "contact":
            values["contact"] = {
                field: values.get("contact_" + field, "")
                for field in ("name", "role", "email", "phone")
            }
        for target, column, default in (
            ("payload", "payload_json", "{}"),
            ("reasons", "reasons_json", "[]"),
        ):
            values[target] = json.loads(values.get(column, default))
        if values.get("suggestion_type") == "contact" and values["payload"]:
            values["contact"] = values["payload"]
        if "candidate_evidence" in tables:
            proofs = connection.execute(
                "SELECT * FROM candidate_evidence WHERE candidate_id=? AND active=1 ORDER BY id LIMIT 50",
                (values["id"],),
            ).fetchall()
            values["evidence"] = [
                {
                    "source": _source(proof["source_path"]),
                    "excerpt": proof["excerpt"],
                    **json.loads(proof["locator_json"] or "{}"),
                }
                for proof in proofs
            ]
            if proofs:
                values["source"] = _source(proofs[0]["source_path"])
                values["excerpt"] = proofs[0]["excerpt"]
            evidence_columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_evidence)")}
            family = "WHEN document_family<>'' THEN 'family:'||document_family " if "document_family" in evidence_columns else ""
            values["evidence_count"] = connection.execute(
                "SELECT COUNT(DISTINCT CASE " + family + "WHEN document_hash<>'' THEN 'hash:'||document_hash ELSE 'path:'||source_path END) "
                "FROM candidate_evidence WHERE candidate_id=? AND active=1",
                (values["id"],),
            ).fetchone()[0]
        suggestions.append(CustomerSuggestion.from_dict(values))
    recognition = {}
    if "customer_recognition_status" in tables:
        row = connection.execute(
            "SELECT * FROM customer_recognition_status WHERE customer_id=?", (customer_id,)
        ).fetchone()
        if row is not None:
            recognition = dict(row)
            recognition["counts"] = json.loads(recognition.pop("counts_json", "{}"))
    return SuggestionPage(
        tuple(suggestions), revision, total, offset + len(rows) < total, recognition, offline=True
    )
