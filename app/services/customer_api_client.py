"""Client-side customer API with durable optimistic offline writes."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request

from app.core.customer_models import Contact, Customer, CustomerJournalEntry


class CustomerApiUnavailable(RuntimeError):
    pass


class CustomerApiConflict(RuntimeError):
    def __init__(self, current: Customer | None):
        super().__init__("Der Kunde wurde auf einem anderen Client geändert.")
        self.current = current


def customer_from_payload(payload: object) -> Customer:
    if not isinstance(payload, dict):
        raise ValueError("Ungültige Kundendaten")
    values = dict(payload)
    values["contacts"] = [Contact(**item) for item in values.get("contacts", [])]
    allowed = Customer.__dataclass_fields__
    return Customer(**{key: value for key, value in values.items() if key in allowed})


class CustomerApiClient:
    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 5):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def save(self, customer: Customer, expected_revision: int) -> Customer:
        method = "POST" if customer.id is None else "PUT"
        path = "/v1/customers" if customer.id is None else f"/v1/customers/{customer.id}"
        payload = {"customer": asdict(customer), "expected_revision": expected_revision}
        response = self._json(method, path, payload)
        return customer_from_payload(response["customer"])

    def delete(self, customer_id: int, expected_revision: int) -> None:
        self._json(
            "DELETE",
            f"/v1/customers/{customer_id}",
            None,
            headers={"If-Match": str(expected_revision)},
            allow_empty=True,
        )

    def action(self, action: str, **values: object) -> object:
        response = self._json(
            "POST", "/v1/customer-actions", {"action": action, **values}
        )
        return response.get("result")

    def add_journal(
        self, customer_id: int, expected_revision: int, body: str, title: str
    ) -> tuple[CustomerJournalEntry | None, int]:
        response = self._json(
            "POST",
            f"/v1/customers/{customer_id}/journal",
            {
                "expected_revision": expected_revision,
                "body": body,
                "title": title,
            },
        )
        result = response.get("result")
        return (
            CustomerJournalEntry(**result) if isinstance(result, dict) else None,
            int(response["revision"]),
        )

    def update_journal(
        self,
        customer_id: int,
        entry_id: int,
        expected_revision: int,
        body: str,
        title: str,
    ) -> tuple[CustomerJournalEntry | None, int]:
        response = self._json(
            "PUT",
            f"/v1/customers/{customer_id}/journal/{entry_id}",
            {
                "expected_revision": expected_revision,
                "body": body,
                "title": title,
            },
        )
        result = response.get("result")
        return (
            CustomerJournalEntry(**result) if isinstance(result, dict) else None,
            int(response["revision"]),
        )

    def delete_journal(
        self, customer_id: int, entry_id: int, expected_revision: int
    ) -> tuple[bool, int]:
        response = self._json(
            "DELETE",
            f"/v1/customers/{customer_id}/journal/{entry_id}",
            None,
            headers={"If-Match": str(expected_revision)},
        )
        return bool(response.get("result")), int(response["revision"])

    def _json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        headers: dict[str, str] | None = None,
        allow_empty: bool = False,
    ) -> dict[str, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.server_url}{path}", data=data, method=method
        )
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                return {} if allow_empty and not raw else json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            if exc.code == 409:
                current = body.get("current")
                raise CustomerApiConflict(
                    customer_from_payload(current) if current is not None else None
                ) from exc
            raise CustomerApiUnavailable(str(body.get("error") or exc)) from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise CustomerApiUnavailable(str(exc)) from exc


class CustomerOfflineQueue:
    """Persist the newest full edit per customer until the server accepts it."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_customer_changes (
                customer_id INTEGER PRIMARY KEY,
                operation TEXT NOT NULL,
                expected_revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_customer_commands (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                expected_revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.commit()

    def enqueue_save(
        self, customer: Customer, expected_revision: int, create: bool = False
    ) -> None:
        if customer.id is None:
            raise ValueError("Neue Kunden benötigen für Offline-Sync eine Server-ID.")
        existing = self.connection.execute(
            "SELECT expected_revision FROM pending_customer_changes WHERE customer_id=?",
            (customer.id,),
        ).fetchone()
        base = int(existing[0]) if existing is not None else expected_revision
        self.connection.execute(
            """
            INSERT INTO pending_customer_changes
                (customer_id, operation, expected_revision, payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                operation=excluded.operation, payload_json=excluded.payload_json
            """,
            (
                customer.id,
                "create" if create else "save",
                base,
                json.dumps(asdict(customer), ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def enqueue_delete(self, customer_id: int, expected_revision: int) -> None:
        existing = self.connection.execute(
            "SELECT expected_revision FROM pending_customer_changes WHERE customer_id=?",
            (customer_id,),
        ).fetchone()
        base = int(existing[0]) if existing is not None else expected_revision
        self.connection.execute(
            """
            INSERT INTO pending_customer_changes
                (customer_id, operation, expected_revision, payload_json)
            VALUES (?, 'delete', ?, '{}')
            ON CONFLICT(customer_id) DO UPDATE SET operation='delete', payload_json='{}'
            """,
            (customer_id, base),
        )
        self.connection.commit()

    def entries(self) -> list[sqlite3.Row]:
        self.connection.row_factory = sqlite3.Row
        return self.connection.execute(
            "SELECT * FROM pending_customer_changes ORDER BY created_at, customer_id"
        ).fetchall()

    def remove(self, customer_id: int) -> None:
        self.connection.execute(
            "DELETE FROM pending_customer_changes WHERE customer_id=?", (customer_id,)
        )
        self.connection.commit()

    def remap_customer(self, previous_id: int, server_id: int) -> None:
        """Point queued dependent commands at an ID assigned by the server."""
        if previous_id == server_id:
            return
        self.connection.execute(
            "UPDATE pending_customer_commands SET customer_id=? WHERE customer_id=?",
            (server_id, previous_id),
        )
        self.connection.commit()

    def enqueue_command(
        self,
        customer_id: int,
        operation: str,
        expected_revision: int,
        payload: dict[str, object],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO pending_customer_commands
                (customer_id, operation, expected_revision, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                customer_id,
                operation,
                expected_revision,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def commands(self) -> list[sqlite3.Row]:
        self.connection.row_factory = sqlite3.Row
        return self.connection.execute(
            "SELECT * FROM pending_customer_commands ORDER BY id"
        ).fetchall()

    def remove_command(self, command_id: int) -> None:
        self.connection.execute(
            "DELETE FROM pending_customer_commands WHERE id=?", (command_id,)
        )
        self.connection.commit()
