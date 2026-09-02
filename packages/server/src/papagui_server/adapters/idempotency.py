"""Focused persistence for mutation idempotency receipts."""

from __future__ import annotations

import json
import sqlite3

from papagui_server.domain.errors import IdempotencyConflictError
from papagui_server.domain.models import CustomerMutationResult


class SqliteIdempotencyRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def result(self, key: str, request_hash: str) -> CustomerMutationResult | None:
        row = self._connection.execute(
            "SELECT request_hash, status_code, response_json "
            "FROM api_idempotency WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_hash"]) != request_hash:
            raise IdempotencyConflictError()
        return CustomerMutationResult(
            json.loads(str(row["response_json"])), int(row["status_code"]), replayed=True
        )

    def remember(
        self,
        key: str,
        request_hash: str,
        operation: str,
        result: CustomerMutationResult,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO api_idempotency
                (idempotency_key, request_hash, operation, status_code, response_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                key,
                request_hash,
                operation,
                result.status_code,
                json.dumps(result.body, ensure_ascii=False, sort_keys=True),
            ),
        )
