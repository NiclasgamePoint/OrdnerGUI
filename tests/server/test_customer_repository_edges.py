from __future__ import annotations

from pathlib import Path

import pytest

from papagui_server.adapters.sqlite_customers import (
    SqliteCustomerUnitOfWork,
    SqliteCustomerUnitOfWorkFactory,
)
from papagui_server.application.customers import CustomerApplicationService
from papagui_server.domain.errors import (
    CustomerConflictError,
    IdempotencyConflictError,
    ResourceNotFoundError,
)


class Publisher:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def publish_customers(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("snapshot failed")
        return {"generation": str(self.calls)}


def _service(tmp_path: Path, publisher=None):
    factory = SqliteCustomerUnitOfWorkFactory(tmp_path / "customers.db")
    factory.initialize()
    return CustomerApplicationService(factory, publisher or Publisher()), factory


def test_customer_children_merge_lookup_and_delete(tmp_path: Path) -> None:
    service, factory = _service(tmp_path)
    created = service.create_customer(
        {
            "display_name": "  Komplett GmbH  ",
            "entity_type": "",
            "company": "Komplett",
            "contacts": [
                {"name": " Ada ", "email": "ada@example.org"},
                "ignored",
            ],
            "service_types": ["Planung", "planung", ""],
            "folder_paths": ["source://primary/a", "source://primary/a"],
            "notes": ["Notiz", "notiz"],
            "tags": ["Wichtig", "wichtig", ""],
        },
        idempotency_key="complete-create-01",
    ).body["customer"]
    assert created["display_name"] == "Komplett GmbH"
    assert created["entity_type"] == "Unternehmen"
    assert created["contacts"][0]["name"] == "Ada"
    assert created["service_types"] == ["Planung"]
    assert created["notes"] == ["Notiz"]
    assert created["tags"] == ["Wichtig"]

    with factory() as work:
        found = work.customers.find_by_display_name("komplett gmbh")
        assert found and found["id"] == created["id"]
        assert work.customers.find_by_display_name("missing") is None

    updated = service.update_customer(
        created["id"],
        {"city": "Berlin", "folder_path": ""},
        expected_revision=1,
        idempotency_key="complete-update-01",
    ).body["customer"]
    assert updated["city"] == "Berlin"
    assert updated["folder_path"] == created["folder_path"]
    with pytest.raises(CustomerConflictError):
        service.delete_customer(
            created["id"], expected_revision=1, idempotency_key="delete-stale-01"
        )
    result = service.delete_customer(
        created["id"], expected_revision=2, idempotency_key="delete-current-01"
    )
    assert result.body == {"deleted": True, "customer_id": created["id"]}
    with pytest.raises(ResourceNotFoundError):
        service.get_customer(created["id"])


def test_all_journal_operations_and_missing_entries(tmp_path: Path) -> None:
    service, _factory = _service(tmp_path)
    customer = service.create_customer(
        {"display_name": "Journal AG"}, idempotency_key="journal-customer-01"
    ).body["customer"]
    first = service.add_journal_entry(
        customer["id"],
        {"title": " Erst ", "body": " Text "},
        expected_revision=1,
        idempotency_key="journal-add-0001",
    )
    entry = first.body["entry"]
    assert entry["entry_number"] == 1
    updated = service.update_journal_entry(
        customer["id"],
        entry["id"],
        {"title": "Neu", "body": "Inhalt"},
        expected_revision=2,
        idempotency_key="journal-update-01",
    )
    assert updated.body["entry"]["title"] == "Neu"
    deleted = service.delete_journal_entry(
        customer["id"],
        entry["id"],
        expected_revision=3,
        idempotency_key="journal-delete-01",
    )
    assert deleted.body["revision"] == 4
    with pytest.raises(ResourceNotFoundError):
        service.update_journal_entry(
            customer["id"],
            999,
            {"title": "x", "body": "x"},
            expected_revision=4,
            idempotency_key="journal-missing-up",
        )
    with pytest.raises(ResourceNotFoundError):
        service.delete_journal_entry(
            customer["id"],
            999,
            expected_revision=4,
            idempotency_key="journal-missing-de",
        )


def test_validation_idempotency_and_publish_retry(tmp_path: Path) -> None:
    publisher = Publisher(fail=True)
    service, factory = _service(tmp_path, publisher)
    with pytest.raises(ValueError, match="Seiteneinteilung"):
        service.list_customers(limit=0)
    with pytest.raises(ValueError, match="Seiteneinteilung"):
        service.list_customers(offset=-1)
    with pytest.raises(ValueError, match="Kundenname"):
        service.create_customer({}, idempotency_key="invalid-customer")
    with pytest.raises(ValueError, match="Idempotency"):
        service.create_customer({"display_name": "X"}, idempotency_key="")
    with pytest.raises(ValueError, match="Idempotency"):
        service.create_customer({"display_name": "X"}, idempotency_key="x" * 201)

    with pytest.raises(RuntimeError, match="snapshot failed"):
        service.create_customer(
            {"display_name": "Committed AG"}, idempotency_key="publish-retry-01"
        )
    assert len(service.list_customers()) == 1
    publisher.fail = False
    replay = service.create_customer(
        {"display_name": "Committed AG"}, idempotency_key="publish-retry-01"
    )
    assert replay.replayed
    assert publisher.calls == 2
    with pytest.raises(IdempotencyConflictError):
        service.create_customer(
            {"display_name": "Different AG"}, idempotency_key="publish-retry-01"
        )

    with factory() as work:
        with pytest.raises(ResourceNotFoundError):
            work.customers._journal(999)


def test_unit_of_work_rolls_back_and_requires_open_transaction(tmp_path: Path) -> None:
    database = tmp_path / "customers.db"
    work = SqliteCustomerUnitOfWork(database)
    with pytest.raises(RuntimeError, match="nicht geöffnet"):
        work.commit()
    with work:
        work.customers.create({"display_name": "Rolled Back"})
    factory = SqliteCustomerUnitOfWorkFactory(database)
    with factory() as read:
        assert read.customers.list() == []

    with pytest.raises(RuntimeError):
        with factory() as failed:
            failed.customers.create({"display_name": "Also Rolled Back"})
            failed.commit()
            raise RuntimeError("late failure")
    with factory() as read:
        # A commit is durable even if later user code fails; this is the explicit
        # transaction boundary and publication happens after it.
        assert read.customers.list()[0]["display_name"] == "Also Rolled Back"


def test_repository_document_suggestion_duplicate(tmp_path: Path) -> None:
    factory = SqliteCustomerUnitOfWorkFactory(tmp_path / "customers.db")
    factory.initialize()
    with factory() as work:
        customer = work.customers.create({"display_name": "Evidence GmbH"})
        values = {
            "kind": "email",
            "value": "mail@example.org",
            "source_path": "source://primary/file.txt",
            "excerpt": "mail@example.org",
            "fingerprint": "fingerprint",
            "confidence": 0.9,
        }
        assert work.customers.add_document_suggestion(customer["id"], **values)
        assert not work.customers.add_document_suggestion(customer["id"], **values)
        work.commit()
