from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from papagui_server.adapters.generations import GenerationV2Publisher
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.application.customers import CustomerApplicationService
from papagui_server.domain.errors import CustomerConflictError


def _service(tmp_path: Path) -> CustomerApplicationService:
    database = tmp_path / "data" / "customers.db"
    factory = SqliteCustomerUnitOfWorkFactory(database)
    factory.initialize()
    return CustomerApplicationService(factory, GenerationV2Publisher(tmp_path / "data"))


def test_parallel_updates_allow_exactly_one_revision_winner(tmp_path: Path) -> None:
    service = _service(tmp_path)
    customer = service.create_customer(
        {"display_name": "Parallel GmbH"}, idempotency_key="parallel-create-001"
    ).body["customer"]

    def update(name: str, key: str):
        try:
            result = service.update_customer(
                customer["id"],
                {**customer, "display_name": name},
                expected_revision=1,
                idempotency_key=key,
            )
            return result.body["customer"]
        except CustomerConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: update(*item),
                [("Client A", "parallel-update-a"), ("Client B", "parallel-update-b")],
            )
        )
    winners = [value for value in results if isinstance(value, dict)]
    conflicts = [value for value in results if isinstance(value, CustomerConflictError)]
    assert len(winners) == 1
    assert winners[0]["revision"] == 2
    assert len(conflicts) == 1
    assert conflicts[0].current["revision"] == 2


def test_journal_mutation_advances_parent_revision(tmp_path: Path) -> None:
    service = _service(tmp_path)
    customer = service.create_customer(
        {"display_name": "Journal GmbH"}, idempotency_key="journal-create-001"
    ).body["customer"]
    created = service.add_journal_entry(
        customer["id"],
        {"title": "Termin", "body": "Besprochen"},
        expected_revision=1,
        idempotency_key="journal-entry-001",
    )
    assert created.status_code == 201
    assert created.body["revision"] == 2
    with pytest.raises(CustomerConflictError):
        service.add_journal_entry(
            customer["id"],
            {"title": "Alt", "body": "Veraltet"},
            expected_revision=1,
            idempotency_key="journal-entry-002",
        )
