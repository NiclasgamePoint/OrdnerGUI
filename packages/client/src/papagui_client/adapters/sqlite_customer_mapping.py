"""Mapping helpers shared by immutable snapshots and durable outboxes."""

from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
from typing import Any, Mapping

from papagui_contracts import Customer, CustomerProject, SourcePath

from papagui_client.application.models import (
    CustomerMutationKind,
    PendingCustomerMutation,
    PendingMutationState,
)


def optional_rows(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...],
) -> list[sqlite3.Row]:
    try:
        return connection.execute(statement, parameters).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc) or "no such column" in str(exc):
            return []
        raise


def hydrate_customer(connection: sqlite3.Connection, row: sqlite3.Row) -> Customer:
    customer_id = int(row["id"])
    values: dict[str, Any] = dict(row)
    values["contacts"] = [
        dict(item)
        for item in optional_rows(
            connection,
            "SELECT name,role,email,phone FROM contacts WHERE customer_id=? ORDER BY id",
            (customer_id,),
        )
    ]
    values["notes"] = [
        str(item["body"])
        for item in optional_rows(
            connection,
            "SELECT body FROM notes WHERE customer_id=? ORDER BY id",
            (customer_id,),
        )
    ]
    values["journal_entries"] = [
        dict(item)
        for item in optional_rows(
            connection,
            "SELECT id,customer_id,entry_number,title,body,created_at,updated_at,"
            "0 AS revision FROM customer_journal_entries WHERE customer_id=? "
            "ORDER BY entry_number,id",
            (customer_id,),
        )
    ]
    values["tags"] = [
        str(item["name"])
        for item in optional_rows(
            connection,
            "SELECT tags.name FROM tags JOIN customer_tags "
            "ON tags.id=customer_tags.tag_id WHERE customer_tags.customer_id=? "
            "ORDER BY tags.name COLLATE NOCASE",
            (customer_id,),
        )
    ]
    projects = _projects(connection, customer_id)
    values["projects"] = projects
    services = [project.service_type for project in projects if project.service_type]
    if not services:
        services = [
            str(item["name"])
            for item in optional_rows(
                connection,
                "SELECT name FROM customer_services WHERE customer_id=? "
                "ORDER BY name COLLATE NOCASE",
                (customer_id,),
            )
        ]
    values["service_types"] = tuple(dict.fromkeys(services))
    portable_folders = [
        f"source://{project.source.source_id}/{project.source.relative_path}"
        for project in projects
        if project.source is not None
    ]
    legacy_folders = [
        str(item["folder_path"])
        for item in optional_rows(
            connection,
            "SELECT folder_path FROM customer_folders WHERE customer_id=? "
            "ORDER BY folder_path COLLATE NOCASE",
            (customer_id,),
        )
    ]
    values["folder_paths"] = tuple(dict.fromkeys(portable_folders or legacy_folders))
    return Customer.from_dict(values)


def _projects(connection: sqlite3.Connection, customer_id: int) -> tuple[CustomerProject, ...]:
    rows = optional_rows(
        connection,
        "SELECT id,customer_id,project_root_id,source_id,relative_path,service_type,"
        "project_label,project_city,year,provenance FROM customer_projects "
        "WHERE customer_id=? ORDER BY year DESC,relative_path COLLATE NOCASE",
        (customer_id,),
    )
    return tuple(
        CustomerProject(
            id=int(row["id"]),
            customer_id=int(row["customer_id"]),
            project_root_id=(
                int(row["project_root_id"])
                if row["project_root_id"] is not None
                else None
            ),
            source=SourcePath(str(row["source_id"]), str(row["relative_path"])),
            service_type=str(row["service_type"]),
            project_label=str(row["project_label"]),
            project_city=str(row["project_city"]),
            year=int(row["year"]) if row["year"] is not None else None,
            provenance=str(row["provenance"]),
        )
        for row in rows
    )


def portable_customer_payload(customer: Customer) -> Customer:
    """Strip machine-specific legacy paths from every outbound mutation."""
    portable = tuple(path for path in customer.folder_paths if path.startswith("source://"))
    primary = customer.folder_path if customer.folder_path.startswith("source://") else ""
    if not primary and portable:
        primary = portable[0]
    return replace(customer, folder_path=primary, folder_paths=portable)


def mutation_from_row(row: sqlite3.Row) -> PendingCustomerMutation:
    kind = CustomerMutationKind(str(row["operation"]))
    payload = json.loads(row["payload_json"])
    if kind is CustomerMutationKind.CREATE:
        payload["id"] = None
    conflict = json.loads(row["conflict_json"]) if row["conflict_json"] else None
    return PendingCustomerMutation(
        sequence=int(row["sequence"]),
        idempotency_key=str(row["idempotency_key"]),
        aggregate_key=str(row["aggregate_key"]),
        operation=kind,
        expected_revision=int(row["expected_revision"]),
        payload=payload,
        state=PendingMutationState(str(row["state"])),
        conflict=conflict,
    )


def customer_json(customer: Customer) -> str:
    return json.dumps(customer.to_dict(), ensure_ascii=False, sort_keys=True)


def customer_from_json(value: str | None) -> Customer | None:
    if not value:
        return None
    payload = json.loads(value)
    return Customer.from_dict(payload) if isinstance(payload, Mapping) else None
