"""Server administration and old suggestions; all records are invented in temporary stores."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from papagui_server.api import create_app
from papagui_server.adapters.customer_schema import initialize_customer_schema
from papagui_server.adapters.customer_suggestions import SqliteCustomerSuggestionRepository
from papagui_server.domain.models import ServerSettings
from tests.server.test_api import _container
from tests.server.test_customer_recognition_jobs import MemoryStore, jobs
from tests.server.test_document_recognition_integration import CONTACT, setup, state


def test_already_migrated_invalid_suggestions_are_hidden_without_touching_decisions():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_customer_schema(connection)
    connection.execute("INSERT INTO customers(id,folder_path,display_name,phone) VALUES (1,'synthetic','Beispiel','030 12345678')")
    SqliteCustomerSuggestionRepository(connection)
    for identifier, kind, value, payload, status in (
        (1, "phone", "03. 01. 2025", {}, "pending"),
        (2, "contact", "Dokumenttitel", {"name": "", "email": "test@example.org"}, "pending"),
        (3, "contact", "Angebot zur Projektplanung", {"name": "Angebot zur Projektplanung"}, "pending"),
        (4, "phone", "03. 01. 2025", {}, "accepted"),
        (5, "contact", "Dokumenttitel", {"name": ""}, "rejected"),
    ):
        connection.execute(
            "INSERT INTO customer_document_suggestions(id,customer_id,kind,value,source_path,excerpt,fingerprint,confidence,"
            "suggestion_type,payload_json,status) VALUES (?,1,?,?,'source://synthetic/example.txt','',?,0,?,?,?)",
            (identifier, kind, value, str(identifier), "contact" if kind == "contact" else "field", json.dumps(payload), status),
        )
    connection.execute("DELETE FROM candidate_schema_metadata WHERE key='candidate_validation_version'")
    repository = SqliteCustomerSuggestionRepository(connection)
    assert repository.list_for_customer(1, status="pending") == []
    assert [row["id"] for row in repository.list_for_customer(1)] == [5, 4]
    assert connection.execute("SELECT phone,revision FROM customers").fetchone()[:] == ("030 12345678", 1)
    assert connection.execute("SELECT count(*) FROM customer_document_suggestions").fetchone()[0] == 5
    assert not repository.add(1, kind="phone", value="03. 01. 2025", source_path="source://synthetic/new.txt",
                              excerpt="", fingerprint="new", confidence=0)
    connection.close()


def test_global_jobs_are_durable_deduplicated_and_scoped():
    store = MemoryStore()
    worker = jobs(store=store)
    requested = worker.request(None, mode="rebuild")
    assert worker.request(None, mode="rebuild") == requested
    worker.request(7)
    with pytest.raises(ValueError):
        worker.request(7, mode="rebuild")
    with pytest.raises(ValueError):
        worker.request(None)
    recovered = jobs(store=store)
    assert recovered.latest(None) == requested
    recovered.cancel(None, requested["id"])
    recovered.run_pending()
    assert recovered.latest(None)["state"] == "cancelled"
    assert recovered.latest(7)["state"] == "completed"


def test_exhaustive_rebuild_evaluates_beyond_normal_budget(tmp_path):
    fixture = setup(tmp_path, {"a.txt": "Synthetische Berechnung", "z.txt": CONTACT},
                    settings=ServerSettings(priority_documents_per_project=1, recognition_documents_per_project_max=1))
    fixture.service.run("primary")
    assert state(fixture)[0]["counts"]["evaluated"] == 1
    result = fixture.service.run("primary", exhaustive=True)
    assert result["evaluated"] == 2
    assert any(row["field_name"] == "phone" for row in state(fixture)[1])


def test_blocked_value_does_not_stop_search_for_missing_field(tmp_path):
    fixture = setup(tmp_path, {"a.txt": CONTACT, "z.txt": CONTACT.replace("kontakt@example.org", "neu@example.net")},
                    settings=ServerSettings(priority_documents_per_project=1, recognition_documents_per_project_max=2))
    with fixture.factory() as work:
        customer = work.customers.get(fixture.customer_id)
        customer.update(company="Beispiel GmbH", phone="030 12345678", street="Beispielweg 1",
                        postal_code="10115", city="Berlin", contacts=[{"name": "Mara Winter"}])
        work.customers.update(customer["id"], customer, customer["revision"])
        work.blocklist.add("email_domain", "example.org")
        work.commit()
    outcome = fixture.service.run("primary")
    assert outcome["evaluated"] == 2
    assert any(row["value"] == "neu@example.net" for row in state(fixture)[1])


def test_admin_api_auth_blocklist_and_global_rebuild(tmp_path):
    container = _container(tmp_path)
    project = container.configuration.source_path / "Planung" / "2026" / "Beispiel GmbH"
    project.mkdir(parents=True)
    (project / "Kontakt.txt").write_text(CONTACT, encoding="utf-8")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(container, manage_lifecycle=False)),
                                     base_url="http://test") as client:
            base = "/v2/admin/recognition"
            for method, path in (("GET", "/blocklist"), ("POST", "/rebuild"), ("GET", "/rebuild")):
                assert (await client.request(method, base + path)).status_code == 401
            client.headers["Authorization"] = "Bearer client-token-123"
            response = await client.post(base + "/blocklist", json={"kind": "email_domain", "value": "example.org"})
            assert response.status_code == 200
            identifier = response.json()["entry"]["id"]
            assert len((await client.get(base + "/blocklist")).json()["entries"]) == 1
            assert (await client.post(base + "/blocklist", json={"kind": "phone", "value": "03. 01. 2025"})).status_code == 400
            assert (await client.get(base + "/rebuild")).json() == {"job": None}
            requested = await client.post(base + "/rebuild")
            assert requested.status_code == 202
            job = requested.json()["job"]
            assert job["mode"] == "rebuild" and job["customer_id"] is None
            assert (await client.post(base + "/rebuild")).json()["job"]["id"] == job["id"]
            await asyncio.to_thread(container.recognition_jobs.run_pending)
            assert (await client.get(base + "/rebuild")).json()["job"]["state"] == "completed"
            customers = container.customers.list_customers()
            assert len(customers) == 1
            customer = customers[0]
            page = container.suggestions.page(customer["id"], status="pending", include_groups=True)
            assert any(row["field_name"] == "phone" for row in page["suggestions"])
            assert not any(row["field_name"] == "email" for row in page["suggestions"])
            assert (await client.delete(base + f"/blocklist/{identifier}")).json() == {"deleted": True}
            assert (await client.delete(base + f"/blocklist/{identifier}")).status_code == 404
            next_job = (await client.post(base + "/rebuild")).json()["job"]
            cancelled = await client.delete(base + "/rebuild/" + next_job["id"])
            assert cancelled.json()["job"]["state"] == "cancelled"

    asyncio.run(scenario())
