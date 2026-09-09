"""Concurrent indexing regressions using only documents invented in this test."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import zipfile

import httpx
import pytest

from papagui_server.api import create_app
from tests.server.test_api import _container


@pytest.mark.parametrize("pause_at", ["extraction", "evaluation"])
def test_blocklist_can_change_during_index_and_remains_effective_after_publication(
    tmp_path, monkeypatch, pause_at
):
    container = _container(tmp_path)
    project = container.configuration.source_path / "Service" / "2026" / "Synthetic GmbH"
    project.mkdir(parents=True)
    (project / "Kontakt.txt").write_text(
        "Auftraggeber: Synthetic GmbH\nE-Mail: synthetic@example.org\nTelefon: 030 12345678",
        encoding="utf-8",
    )
    assert container.coordinator.run_once(full_rebuild=True) == 0
    customer, = container.customers.list_customers()
    customer_id = customer["id"]
    phone, = [row for row in container.suggestions.page(customer_id, status="pending")["suggestions"]
              if row["field_name"] == "phone"]
    before = container.publisher.current()
    # A catalog rebuild can reuse cached extraction for an unchanged document.
    (project / "Kontakt.txt").write_text(
        "Auftraggeber: Synthetic GmbH\nE-Mail: synthetic@example.org\nTelefon: 030 12345678\n"
        "Synthetische Aktualisierung für die erneute Extraktion.",
        encoding="utf-8",
    )
    paused = threading.Event()
    resume = threading.Event()

    if pause_at == "extraction":
        target = container.coordinator._catalog.extractor
        attribute = "extract_document"
    else:
        from papagui_server.application import document_recognition

        target = document_recognition
        attribute = "document_candidates"
    original = getattr(target, attribute)

    def pause_after_work(*args, **kwargs):
        result = original(*args, **kwargs)
        paused.set()
        assert resume.wait(10), "Test did not release the paused index run"
        return result

    monkeypatch.setattr(target, attribute, pause_after_work)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(container.coordinator.run_once(full_rebuild=True))
    )
    worker.start()

    async def mutate_while_index_is_paused():
        app = create_app(container, manage_lifecycle=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer client-token-123"},
        ) as client:
            endpoint = "/v2/admin/recognition/blocklist"
            payload = {"kind": "phone", "value": "+49 30 12345678", "reason": "Synthetic switchboard"}
            added = await client.post(endpoint, json=payload)
            assert added.status_code == 200, added.text
            entry = added.json()["entry"]
            listed = await client.get(endpoint)
            assert listed.status_code == 200
            assert listed.json()["entries"] == [entry]
            suggestions = await client.get(f"/v2/customers/{customer_id}/suggestions?status=pending")
            assert suggestions.status_code == 200
            assert phone["id"] not in {row["id"] for row in suggestions.json()["suggestions"]}
            first_publication = container.publisher.current()
            assert first_publication["components"]["customers"] != before["components"]["customers"]
            assert first_publication["components"]["index"] == before["components"]["index"]

            removed = await client.delete(f"{endpoint}/{entry['id']}")
            assert removed.status_code == 200
            assert removed.json() == {"deleted": True}
            listed = await client.get(endpoint)
            assert listed.json()["entries"] == []
            suggestions = await client.get(f"/v2/customers/{customer_id}/suggestions?status=pending")
            assert phone["id"] in {row["id"] for row in suggestions.json()["suggestions"]}

            readded = await client.post(endpoint, json=payload)
            assert readded.status_code == 200, readded.text
            assert not resume.is_set()
            assert worker.is_alive()
            return readded.json()["entry"]

    try:
        assert paused.wait(10), "Index run did not reach the controlled pause"
        # This thread cannot acquire the index lock while the worker is paused.
        acquired = container.coordinator.operation_lock.acquire(blocking=False)
        if acquired:
            container.coordinator.operation_lock.release()
        assert not acquired
        entry = asyncio.run(mutate_while_index_is_paused())
    finally:
        resume.set()
        worker.join(10)
    assert not worker.is_alive()
    assert results == [0]
    assert container.recognition_blocklist.list() == [entry]
    assert phone["id"] not in {
        row["id"] for row in container.suggestions.page(customer_id, status="pending")["suggestions"]
    }

    latest = container.publisher.current()
    assert latest["components"]["index"] != before["components"]["index"]
    descriptor = latest["components"]["customers"]
    archive = container.publisher.archive_path("customers", descriptor["generation"])
    published = tmp_path / "synthetic-published-customers.sqlite"
    with zipfile.ZipFile(archive) as bundle:
        published.write_bytes(bundle.read("customers.db"))
    with sqlite3.connect(published) as connection:
        assert connection.execute(
            "SELECT normalized_value FROM recognition_blocklist"
        ).fetchall() == [("+493012345678",)]
        assert connection.execute(
            "SELECT status,lifecycle FROM customer_document_suggestions WHERE id=?", (phone["id"],)
        ).fetchone() == ("pending", "blocked")
        assert connection.execute(
            "SELECT count(*) FROM candidate_evidence WHERE candidate_id=? AND active=1", (phone["id"],)
        ).fetchone()[0] == 1
