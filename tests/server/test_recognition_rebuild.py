"""Full rebuild regressions using only documents and customer data invented here."""

from __future__ import annotations

import threading
from unittest.mock import Mock

import pytest

from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.composition import build_container
from tests.server.test_api import _container


def setup_rebuild(tmp_path):
    container = _container(tmp_path)
    project = container.configuration.source_path / "Service" / "2026" / "Synthetic GmbH"
    project.mkdir(parents=True)
    documents = [project / "Kontakt-A.txt", project / "Kontakt-B.txt"]
    documents[0].write_text(
        "Auftraggeber: Synthetic GmbH\nE-Mail: accepted@example.org\nTelefon: 030 12345678",
        encoding="utf-8",
    )
    documents[1].write_text(
        "Auftraggeber: Synthetic GmbH\nE-Mail: second@example.org", encoding="utf-8"
    )
    assert container.coordinator.run_once(full_rebuild=True) == 0
    factory = SqliteCustomerUnitOfWorkFactory(container.configuration.data_path / "customers.db")
    with factory() as work:
        customer, = work.customers.list()
        for kind, value, action in (("email", "accepted@example.org", "accept"),
                                    ("phone", "030 12345678", "reject")):
            suggestion, = [row for row in work.suggestions.list_for_customer(customer["id"], status="pending")
                           if row["field_name"] == kind and row["value"] == value]
            work.suggestions.decide(customer["id"], suggestion["id"], action=action,
                                    expected_revision=customer["revision"], current_customer=customer)
            customer = work.customers.get(customer["id"])
        customer = work.customers.update(customer["id"], {
            **customer, "company": "Manual Synthetic Company", "phone": "+44 20 7946 0958",
            "street": "Manual Street 12", "postal_code": "10115", "city": "Berlin",
            "contacts": [{"name": "Manual Person", "role": "Planning", "email": "manual@example.org",
                          "phone": "+49 30 12345678"}],
        }, expected_revision=customer["revision"])
        work.commit()
    return container, factory, customer["id"], documents


def protected_state(factory, customer_id):
    with factory() as work:
        connection = work._connection
        tables = ("customers", "contacts", "customer_field_provenance", "candidate_decisions")
        return {
            "tables": {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
                       for table in tables},
            "accepted": [(row["id"], row["value"]) for row in
                         work.suggestions.list_for_customer(customer_id, status="accepted")],
            "rejected": [(row["id"], row["value"]) for row in
                         work.suggestions.list_for_customer(customer_id, status="rejected")],
        }


def test_repeated_full_rebuild_reextracts_unchanged_documents_and_preserves_manual_data(tmp_path, monkeypatch):
    container, factory, customer_id, documents = setup_rebuild(tmp_path)
    before = protected_state(factory, customer_id)
    # A full rebuild must evaluate every document despite an ordinary recheck's
    # one-document cap, even when every customer field is already populated.
    container.settings.update({"priority_documents_per_project": 1,
                               "recognition_documents_per_project_max": 1})
    indexer = container.coordinator._catalog
    extract = Mock(wraps=indexer.extractor.extract_document)
    monkeypatch.setattr(indexer.extractor, "extract_document", extract)
    publication = Mock(wraps=container.publisher.publish_all)
    monkeypatch.setattr(container.publisher, "publish_all", publication)

    previous_ids = set()
    for iteration in range(2):
        job = container.recognition_jobs.request(None, mode="rebuild")
        assert job["id"] not in previous_ids
        previous_ids.add(job["id"])
        assert container.recognition_jobs.request(None, mode="rebuild")["id"] == job["id"]
        container.recognition_jobs.run_pending()
        assert container.recognition_jobs.latest(None)["state"] == "completed"
        assert extract.call_count == len(documents) * (iteration + 1)
        assert {call.args[0] for call in extract.call_args_list} == set(documents)
        assert publication.call_count == iteration + 1
        assert protected_state(factory, customer_id) == before
        status = container.suggestions.recognition_status(customer_id)
        assert status["counts"]["evaluated"] == len(documents)
        assert status["state"] == "complete"
        with factory() as work:
            pending = work.suggestions.list_for_customer(customer_id, status="pending")
            assert "second@example.org" in {row["value"] for row in pending}
            assert not {"accepted@example.org", "030 12345678"}.intersection(row["value"] for row in pending)


def test_cancel_during_fresh_extraction_keeps_active_catalog_and_protected_data(tmp_path, monkeypatch):
    container, factory, customer_id, documents = setup_rebuild(tmp_path)
    before = protected_state(factory, customer_id)
    catalog_before = container.catalog.snapshot_version()
    publication = Mock(wraps=container.publisher.publish_all)
    monkeypatch.setattr(container.publisher, "publish_all", publication)
    original_extract = container.coordinator._catalog.extractor.extract_document
    job = container.recognition_jobs.request(None, mode="rebuild")

    def cancel_after_read(path, settings, cancelled):
        result = original_extract(path, settings, cancelled)
        container.recognition_jobs.cancel(None, job["id"])
        return result

    monkeypatch.setattr(container.coordinator._catalog.extractor, "extract_document", cancel_after_read)
    documents[1].write_text("Auftraggeber: Synthetic GmbH\nE-Mail: replacement@example.org", encoding="utf-8")
    container.recognition_jobs.run_pending()
    assert container.recognition_jobs.latest(None)["state"] == "cancelled"
    assert container.catalog.snapshot_version() == catalog_before
    assert protected_state(factory, customer_id) == before
    publication.assert_not_called()


def test_interrupted_global_job_recovers_after_restart_with_fresh_extraction(tmp_path, monkeypatch):
    container, factory, customer_id, documents = setup_rebuild(tmp_path)
    before = protected_state(factory, customer_id)
    job = container.recognition_jobs.request(None, mode="rebuild")
    # Persist the state at the crash boundary, without reading any external data.
    container.recognition_jobs._jobs[-1]["state"] = "running"
    container.recognition_jobs._persist()
    recovered = build_container(container.configuration)
    assert recovered.recognition_jobs.latest(None)["id"] == job["id"]
    assert recovered.recognition_jobs.latest(None)["state"] == "queued"
    extractor = recovered.coordinator._catalog.extractor
    extract = Mock(wraps=extractor.extract_document)
    monkeypatch.setattr(extractor, "extract_document", extract)
    recovered.recognition_jobs.run_pending()
    assert recovered.recognition_jobs.latest(None)["state"] == "completed"
    assert extract.call_count == len(documents)
    assert protected_state(factory, customer_id) == before


@pytest.mark.parametrize("signal", ["stop", "restart"])
def test_index_waiting_behind_rebuild_never_starts_after_shutdown(tmp_path, monkeypatch, signal):
    container = _container(tmp_path)
    coordinator = container.coordinator
    original_lock = coordinator.operation_lock
    attempted = threading.Event()

    class ObservedLock:
        def acquire(self, **kwargs):
            attempted.set()
            return original_lock.acquire(**kwargs)

        def release(self):
            original_lock.release()

    monkeypatch.setattr(coordinator, "operation_lock", ObservedLock())
    build = Mock()
    publication = Mock()
    monkeypatch.setattr(coordinator._catalog, "build", build)
    monkeypatch.setattr(container.publisher, "publish_all", publication)
    results = []
    worker = threading.Thread(target=lambda: results.append(coordinator.run_once(full_rebuild=True)))
    with original_lock:
        worker.start()
        assert attempted.wait(1)
        if signal == "stop":
            coordinator.stop(timeout=0)
        else:
            coordinator.request_restart()
        worker.join(1)
        assert not worker.is_alive()
    assert len(results) == 1 and results[0] != 0
    build.assert_not_called()
    publication.assert_not_called()
