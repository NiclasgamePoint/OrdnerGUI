"""Local customer cache backed by the authoritative server API."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.customer_models import Customer
from app.core.customer_repository import CustomerRepository
from app.services.customer_api_client import (
    CustomerApiClient,
    CustomerApiConflict,
    CustomerApiUnavailable,
    CustomerOfflineQueue,
    customer_from_payload,
)


class SyncedCustomerRepository(CustomerRepository):
    """Keep reads local while routing customer writes through the server."""

    def __init__(
        self,
        database_path: Path,
        server_url: str,
        queue_path: Path,
        token: str = "",
    ) -> None:
        super().__init__(database_path)
        self.api = CustomerApiClient(server_url, token=token)
        self.offline_queue = CustomerOfflineQueue(queue_path)

    def save(
        self,
        customer: Customer,
        commit: bool = True,
        expected_revision: int | None = None,
    ) -> Customer:
        base_revision = (
            expected_revision if expected_revision is not None else customer.revision
        )
        local_id = customer.id
        try:
            server_customer = self.api.save(customer, base_revision)
        except CustomerApiConflict:
            raise
        except CustomerApiUnavailable:
            local = super().save(customer, commit=commit)
            self.offline_queue.enqueue_save(
                local,
                base_revision,
                create=local_id is None,
            )
            return local
        return self._store_server_customer(server_customer, previous_id=local_id)

    def delete(self, customer_id: int, expected_revision: int | None = None):
        current = self.get(customer_id)
        revision = expected_revision if expected_revision is not None else (
            current.revision if current is not None else 0
        )
        try:
            self.api.delete(customer_id, revision)
        except CustomerApiConflict:
            raise
        except CustomerApiUnavailable:
            self.offline_queue.enqueue_delete(customer_id, revision)
        super().delete(customer_id)

    def clear_all_customer_data(self):
        result = self.api.action("clear_all_customer_data")
        super().clear_all_customer_data()
        return result

    def set_blacklist_suggestion_status(self, suggestion_id: int, status: str):
        result = self.api.action(
            "set_blacklist_suggestion_status",
            suggestion_id=suggestion_id,
            status=status,
        )
        super().set_blacklist_suggestion_status(suggestion_id, status)
        return result

    def resolve_data_suggestion(self, suggestion_id: int, accepted: bool):
        result = self.api.action(
            "resolve_data_suggestion",
            suggestion_id=suggestion_id,
            accepted=accepted,
        )
        # The immutable generation is the canonical local refresh. Mirroring the
        # operation here keeps the open dialog coherent until that sync finishes.
        local = super().resolve_data_suggestion(suggestion_id, accepted)
        if isinstance(result, dict):
            return customer_from_payload(result)
        return local

    def flush_offline_changes(self) -> list[CustomerApiConflict]:
        conflicts: list[CustomerApiConflict] = []
        for entry in self.offline_queue.entries():
            customer_id = int(entry["customer_id"])
            try:
                if entry["operation"] == "delete":
                    self.api.delete(customer_id, int(entry["expected_revision"]))
                else:
                    customer = customer_from_payload(json.loads(entry["payload_json"]))
                    previous_id = customer.id
                    if entry["operation"] == "create":
                        customer.id = None
                    saved = self.api.save(customer, int(entry["expected_revision"]))
                    if previous_id is not None and saved.id is not None:
                        self.offline_queue.remap_customer(previous_id, int(saved.id))
                    self._store_server_customer(saved, previous_id=previous_id)
            except CustomerApiConflict as exc:
                conflicts.append(exc)
                continue
            except CustomerApiUnavailable:
                break
            self.offline_queue.remove(customer_id)
        for command in self.offline_queue.commands():
            try:
                self._send_journal_command(
                    str(command["operation"]),
                    int(command["customer_id"]),
                    int(command["expected_revision"]),
                    json.loads(command["payload_json"]),
                )
            except CustomerApiConflict as exc:
                conflicts.append(exc)
                continue
            except CustomerApiUnavailable:
                break
            self.offline_queue.remove_command(int(command["id"]))
        return conflicts

    def add_journal_entry(self, customer_id: int, body: str, title: str = ""):
        customer = self.get(customer_id)
        revision = customer.revision if customer is not None else 0
        try:
            result, server_revision = self.api.add_journal(
                customer_id, revision, body, title
            )
        except CustomerApiUnavailable:
            result = super().add_journal_entry(customer_id, body, title)
            self._bump_local_revision(customer_id)
            self.offline_queue.enqueue_command(
                customer_id, "journal_add", revision, {"body": body, "title": title}
            )
            return result
        local = super().add_journal_entry(customer_id, body, title)
        self._set_local_revision(customer_id, server_revision)
        return result or local

    def update_journal_entry(
        self, customer_id: int, entry_id: int, body: str, title: str = ""
    ):
        customer = self.get(customer_id)
        revision = customer.revision if customer is not None else 0
        try:
            result, server_revision = self.api.update_journal(
                customer_id, entry_id, revision, body, title
            )
        except CustomerApiUnavailable:
            result = super().update_journal_entry(customer_id, entry_id, body, title)
            self._bump_local_revision(customer_id)
            self.offline_queue.enqueue_command(
                customer_id,
                "journal_update",
                revision,
                {"entry_id": entry_id, "body": body, "title": title},
            )
            return result
        local = super().update_journal_entry(customer_id, entry_id, body, title)
        self._set_local_revision(customer_id, server_revision)
        return result or local

    def delete_journal_entry(self, customer_id: int, entry_id: int) -> bool:
        customer = self.get(customer_id)
        revision = customer.revision if customer is not None else 0
        try:
            result, server_revision = self.api.delete_journal(
                customer_id, entry_id, revision
            )
        except CustomerApiUnavailable:
            result = super().delete_journal_entry(customer_id, entry_id)
            self._bump_local_revision(customer_id)
            self.offline_queue.enqueue_command(
                customer_id, "journal_delete", revision, {"entry_id": entry_id}
            )
            return result
        super().delete_journal_entry(customer_id, entry_id)
        self._set_local_revision(customer_id, server_revision)
        return result

    def _send_journal_command(
        self,
        operation: str,
        customer_id: int,
        expected_revision: int,
        payload: dict[str, object],
    ) -> None:
        if operation == "journal_add":
            _result, revision = self.api.add_journal(
                customer_id,
                expected_revision,
                str(payload.get("body", "")),
                str(payload.get("title", "")),
            )
        elif operation == "journal_update":
            _result, revision = self.api.update_journal(
                customer_id,
                int(payload["entry_id"]),
                expected_revision,
                str(payload.get("body", "")),
                str(payload.get("title", "")),
            )
        else:
            _result, revision = self.api.delete_journal(
                customer_id, int(payload["entry_id"]), expected_revision
            )
        self._set_local_revision(customer_id, revision)

    def _bump_local_revision(self, customer_id: int) -> None:
        self.connection.execute(
            "UPDATE customers SET revision=revision+1 WHERE id=?", (customer_id,)
        )
        self.connection.commit()

    def _set_local_revision(self, customer_id: int, revision: int) -> None:
        self.connection.execute(
            "UPDATE customers SET revision=? WHERE id=?", (revision, customer_id)
        )
        self.connection.commit()

    def _store_server_customer(
        self, customer: Customer, previous_id: int | None = None
    ) -> Customer:
        if previous_id is not None and previous_id != customer.id:
            super().delete(previous_id)
        if customer.id is None:
            raise ValueError("Serverantwort enthält keine Kunden-ID.")
        if self.get(customer.id) is None:
            self.connection.execute(
                """
                INSERT INTO customers
                    (id, folder_path, display_name, entity_type, company, email, phone,
                     street, postal_code, city, revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer.id,
                    customer.folder_path or f"customer://server-{customer.id}",
                    customer.display_name,
                    customer.entity_type,
                    customer.company,
                    customer.email,
                    customer.phone,
                    customer.street,
                    customer.postal_code,
                    customer.city,
                    max(0, customer.revision - 1),
                ),
            )
            self.connection.commit()
        saved = super().save(customer)
        self.connection.execute(
            "UPDATE customers SET revision=? WHERE id=?",
            (customer.revision, customer.id),
        )
        self.connection.commit()
        return self.get(int(customer.id)) or saved
