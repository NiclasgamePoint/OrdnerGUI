"""Focused SQLite persistence for recognition cases, decisions, and run history."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from papagui_contracts import (
    RecognitionCase,
    RecognitionCaseStatus,
    RecognitionDecision,
    RecognitionDecisionAction,
    RecognitionRunSummary,
)

from papagui_server.domain.errors import ResourceNotFoundError


class SqliteRecognitionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def begin_run(self) -> int:
        cursor = self._connection.execute(
            "INSERT INTO recognition_run_history(started_at) VALUES (?)",
            (_now(),),
        )
        return int(cursor.lastrowid)

    def finish_run(
        self, run_id: int, values: dict[str, int], *, error: str = ""
    ) -> dict[str, Any]:
        self._connection.execute(
            """
            UPDATE recognition_run_history SET
                detected=?, created=?, assigned=?, pending=?, rejected=?,
                error=?, finished_at=? WHERE id=?
            """,
            (
                int(values.get("detected", 0)),
                int(values.get("created", 0)),
                int(values.get("assigned", 0)),
                int(values.get("pending", 0)),
                int(values.get("rejected", 0)),
                str(error),
                _now(),
                run_id,
            ),
        )
        return self._run(run_id).to_dict()

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("Ungültige Anzahl von Erkennungsläufen.")
        rows = self._connection.execute(
            "SELECT * FROM recognition_run_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._map_run(row).to_dict() for row in rows]

    def save_case(self, case: RecognitionCase, *, run_id: int) -> bool:
        previous = self.decision(case.signature)
        status = (
            RecognitionCaseStatus.REJECTED.value
            if previous is not None
            and previous.action is RecognitionDecisionAction.REJECT
            else (
                RecognitionCaseStatus.RESOLVED.value
                if previous is not None
                else RecognitionCaseStatus.PENDING.value
            )
        )
        self._connection.execute(
            """
            INSERT INTO recognition_cases(
                signature, recognition_key, display_name, payload_json,
                project_roots_json, cities_json, service_types_json, years_json,
                reason, suggested_customer_ids_json, evidence_json, status,
                last_seen_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
                recognition_key=excluded.recognition_key,
                display_name=excluded.display_name,
                payload_json=excluded.payload_json,
                project_roots_json=excluded.project_roots_json,
                cities_json=excluded.cities_json,
                service_types_json=excluded.service_types_json,
                years_json=excluded.years_json,
                reason=excluded.reason,
                suggested_customer_ids_json=excluded.suggested_customer_ids_json,
                evidence_json=excluded.evidence_json,
                status=CASE
                    WHEN recognition_cases.status IN ('resolved', 'rejected')
                    THEN recognition_cases.status ELSE excluded.status END,
                last_seen_run_id=excluded.last_seen_run_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                case.signature,
                case.recognition_key,
                case.display_name,
                case.to_json(),
                json.dumps([item.to_dict() for item in case.project_roots]),
                json.dumps(list(case.cities), ensure_ascii=False),
                json.dumps(list(case.service_types), ensure_ascii=False),
                json.dumps(list(case.years)),
                case.reason,
                json.dumps(list(case.suggested_customer_ids)),
                json.dumps([item.to_dict() for item in case.evidence], ensure_ascii=False),
                status,
                run_id,
            ),
        )
        return previous is None

    def mark_unseen_stale(self, run_id: int) -> None:
        self._connection.execute(
            "UPDATE recognition_cases SET status='stale', "
            "updated_at=CURRENT_TIMESTAMP WHERE status='pending' "
            "AND COALESCE(last_seen_run_id, -1)<>?",
            (run_id,),
        )

    def list_cases(self, *, status: str | None = None) -> list[dict[str, Any]]:
        parameters: tuple[Any, ...] = ()
        condition = ""
        if status is not None:
            try:
                normalized = RecognitionCaseStatus(status).value
            except ValueError as error:
                raise ValueError("Unbekannter Erkennungsstatus.") from error
            condition = " WHERE status=?"
            parameters = (normalized,)
        rows = self._connection.execute(
            "SELECT * FROM recognition_cases" + condition
            + " ORDER BY recognition_key, created_at, signature",
            parameters,
        ).fetchall()
        return [self._map_case(row).to_dict() for row in rows]

    def get_case(self, signature: str) -> RecognitionCase:
        row = self._connection.execute(
            "SELECT * FROM recognition_cases WHERE signature=?", (signature,)
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Erkennungsfall nicht gefunden.")
        return self._map_case(row)

    def decision(self, signature: str) -> RecognitionDecision | None:
        row = self._connection.execute(
            "SELECT signature, action, customer_id, decided_at "
            "FROM recognition_decisions WHERE signature=?",
            (signature,),
        ).fetchone()
        if row is None:
            return None
        action = str(row["action"])
        if action == "ignore":
            action = RecognitionDecisionAction.REJECT.value
        return RecognitionDecision(
            signature=str(row["signature"]),
            action=RecognitionDecisionAction(action),
            customer_id=(
                int(row["customer_id"]) if row["customer_id"] is not None else None
            ),
            decided_at=str(row["decided_at"]),
        )

    def save_decision(
        self,
        signature: str,
        *,
        action: RecognitionDecisionAction,
        customer_id: int | None,
    ) -> dict[str, Any]:
        self.get_case(signature)
        decided_at = _now()
        self._connection.execute(
            """
            INSERT INTO recognition_decisions(signature, action, customer_id, decided_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET action=excluded.action,
                customer_id=excluded.customer_id, decided_at=excluded.decided_at
            """,
            (signature, action.value, customer_id, decided_at),
        )
        status = (
            RecognitionCaseStatus.REJECTED.value
            if action is RecognitionDecisionAction.REJECT
            else RecognitionCaseStatus.RESOLVED.value
        )
        self._connection.execute(
            "UPDATE recognition_cases SET status=?, updated_at=? WHERE signature=?",
            (status, decided_at, signature),
        )
        return RecognitionDecision(
            signature=signature,
            action=action,
            customer_id=customer_id,
            decided_at=decided_at,
        ).to_dict()

    def _run(self, run_id: int) -> RecognitionRunSummary:
        row = self._connection.execute(
            "SELECT * FROM recognition_run_history WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("Erkennungslauf nicht gefunden.")
        return self._map_run(row)

    @staticmethod
    def _map_run(row: sqlite3.Row) -> RecognitionRunSummary:
        return RecognitionRunSummary(
            id=int(row["id"]),
            detected=int(row["detected"]),
            created=int(row["created"]),
            assigned=int(row["assigned"]),
            pending=int(row["pending"]),
            rejected=int(row["rejected"]),
            error=str(row["error"]),
            started_at=str(row["started_at"]),
            finished_at=str(row["finished_at"]),
        )

    @staticmethod
    def _map_case(row: sqlite3.Row) -> RecognitionCase:
        payload = {
            "signature": str(row["signature"]),
            "recognition_key": str(row["recognition_key"]),
            "display_name": str(row["display_name"]),
            "project_roots": json.loads(str(row["project_roots_json"])),
            "cities": json.loads(str(row["cities_json"])),
            "service_types": json.loads(str(row["service_types_json"])),
            "years": json.loads(str(row["years_json"])),
            "reason": str(row["reason"]),
            "suggested_customer_ids": json.loads(
                str(row["suggested_customer_ids_json"])
            ),
            "evidence": json.loads(str(row["evidence_json"])),
            "status": str(row["status"]),
        }
        return RecognitionCase.from_dict(payload)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
