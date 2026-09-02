"""Cross-snapshot customer search and deterministic global result ordering."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Callable

from papagui_client.application.models import (
    GlobalSearchKind,
    GlobalSearchQuery,
    GlobalSearchRecord,
    GlobalSearchSort,
)

from .sqlite_catalog_schema import columns, read_only, tables


def customer_records(
    database: Callable[[], Path | None] | None,
    query: GlobalSearchQuery,
) -> list[GlobalSearchRecord]:
    if database is None:
        return []
    value = database()
    if value is None or not Path(value).resolve().is_file():
        return []
    with closing(read_only(Path(value), "Customer")) as connection:
        if "customers" not in tables(connection):
            return []
        customer_columns = columns(connection, "customers")
        if not {"id", "display_name"} <= customer_columns:
            return []
        selected = [
            name
            for name in (
                "id",
                "display_name",
                "company",
                "city",
                "email",
                "phone",
                "revision",
            )
            if name in customer_columns
        ]
        searchable = [
            name
            for name in ("display_name", "company", "city", "email")
            if name in customer_columns
        ]
        clauses = []
        parameters: list[object] = []
        if query.text.strip():
            clauses.append(
                "(" + " OR ".join(f"{name} LIKE ?" for name in searchable) + ")"
            )
            parameters.extend(f"%{query.text.strip()}%" for _ in searchable)
        if query.customer_name:
            clauses.append("display_name LIKE ?")
            parameters.append(f"%{query.customer_name}%")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            f"SELECT {','.join(selected)} FROM customers {where}", parameters
        ).fetchall()
    return [
        GlobalSearchRecord(
            GlobalSearchKind.CUSTOMER,
            f"customer:{int(row['id'])}",
            str(row["display_name"]),
            " · ".join(
                str(row[name])
                for name in ("company", "city", "email")
                if name in row.keys() and row[name]
            ),
            customer_id=int(row["id"]),
            metadata={name: row[name] for name in row.keys()},
        )
        for row in rows
    ]


def sort_records(records: list[GlobalSearchRecord], query: GlobalSearchQuery) -> None:
    if query.sort is GlobalSearchSort.NAME:
        records.sort(key=lambda item: (item.title.casefold(), item.kind.value, item.key))
        return
    if query.sort is GlobalSearchSort.MODIFIED:
        records.sort(key=lambda item: (item.title.casefold(), item.key))
        records.sort(key=lambda item: item.modified_date, reverse=True)
        return
    term = query.text.strip().casefold()

    def relevance(item: GlobalSearchRecord) -> tuple[int, int, str, str]:
        title = item.title.casefold()
        subtitle = item.subtitle.casefold()
        rank = (
            0
            if term and title == term
            else 1
            if term and title.startswith(term)
            else 2
            if term and term in title
            else 3
            if term and term in subtitle
            else 4
        )
        return rank, tuple(GlobalSearchKind).index(item.kind), title, item.key

    records.sort(key=relevance)
