"""Manage global recognition exclusions and publish the updated review snapshot."""

from __future__ import annotations

from typing import Any

from papagui_server.application.ports import CustomerUnitOfWorkFactory, GenerationPublisherPort
from papagui_server.domain.errors import ResourceNotFoundError


class RecognitionBlocklistApplicationService:
    def __init__(self, unit_of_work: CustomerUnitOfWorkFactory,
                 publisher: GenerationPublisherPort) -> None:
        self._unit_of_work = unit_of_work
        self._publisher = publisher

    def list(self) -> list[dict[str, Any]]:
        with self._unit_of_work() as work:
            return work.blocklist.list()

    def list_state(self) -> dict[str, Any]:
        with self._unit_of_work() as work:
            return {"entries": work.blocklist.list(),
                    "publication_pending": work.blocklist.pending_publication() is not None}

    def add(self, kind: str, value: str, reason: str = "") -> dict[str, Any]:
        # SQLite serializes this short change with evidence writes. The long
        # extraction/index lock must not prevent users from managing exclusions.
        with self._unit_of_work() as work:
            entry = work.blocklist.add(kind, value, reason)
            self._reconcile_restored(work)
            work.commit()
        # The publisher protects snapshot creation and activation with its own
        # lock, after the write transaction has released the database.
        self._publisher.publish_customers()
        return entry

    def delete(self, entry_id: int) -> bool:
        with self._unit_of_work() as work:
            if not work.blocklist.delete(entry_id):
                raise ResourceNotFoundError("Ausschluss nicht gefunden.")
            self._reconcile_restored(work)
            work.commit()
        self._publisher.publish_customers()
        return True

    def batch(self, additions: list[dict[str, Any]], deletions: list[int]) -> dict[str, Any]:
        # BEGIN IMMEDIATE serializes this delta with recognition writes and other
        # editors without acquiring the long-running index/extraction lock.
        with self._unit_of_work() as work:
            changed = work.blocklist.apply_batch(additions, deletions)
            if changed:
                self._reconcile_restored(work)
                token = work.blocklist.mark_publication_pending()
            else:
                token = work.blocklist.pending_publication()
            entries = work.blocklist.list()
            work.commit()
        published = True
        if token is not None:
            try:
                self._publisher.publish_customers()
                with self._unit_of_work() as work:
                    work.blocklist.clear_pending_publication(token)
                    work.commit()
            except Exception:
                # Changes already committed are still successful. The durable
                # marker lets an unchanged retry finish publication, including
                # after a restart, without rerunning recognition or extraction.
                published = False
        return {"entries": entries, "changed": changed, "published": published}

    @staticmethod
    def _reconcile_restored(work) -> None:
        # A value may have been entered manually while its suggestion was
        # blocked. Resolve that visibility before publishing an offline snapshot.
        for customer_id in work.blocklist.restored_customer_ids:
            customer = work.customers.get(customer_id)
            if customer is not None:
                work.suggestions.reconcile_customer(customer)
