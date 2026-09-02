from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.composition import RuntimeConfiguration, build_container
from papagui_server.api import create_app
from papagui_server.domain.errors import (
    CustomerConflictError,
    ResourceNotFoundError,
    SuggestionOverwriteError,
)
from papagui_server.domain.models import ServerSettings
from tests.tools.golden_fixtures import materialize_sqlite_fixture


def _ambiguous_container(tmp_path: Path):
    source = tmp_path / "source"
    for service, year, label in (
        ("Planung", "2026", "Alpha GmbH, Köln"),
        ("Beratung", "2025", "Alpha GmbH, Bonn"),
    ):
        folder = source / service / year / label
        folder.mkdir(parents=True)
        (folder / "Kontakt.txt").write_text(
            "Kontakt alpha@example.org Telefon 0221 1234567", encoding="utf-8"
        )
    data = tmp_path / "data"
    SqliteCatalogIndexer(data).build(
        source,
        source_id="primary",
        full_rebuild=True,
        settings=ServerSettings(),
        cancelled=lambda: False,
        progress=lambda *_args: None,
    )
    return build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=data,
            config_path=tmp_path / "config",
            client_token="recognition-client-token",
            bootstrap_admin_password="recognition-admin-password",
        )
    )


def _seed_customer(container, **values) -> dict:
    factory = SqliteCustomerUnitOfWorkFactory(
        container.configuration.data_path / "customers.db"
    )
    with factory() as work:
        customer = work.customers.create(
            {"display_name": "Alpha GmbH", **values}
        )
        work.commit()
    return customer


def test_ambiguous_cities_are_persisted_rejected_and_not_reopened(tmp_path: Path) -> None:
    container = _ambiguous_container(tmp_path)
    result = container.recognition.synchronize(
        container.configuration.source_path,
        source_id="primary",
        minimum_year=2016,
    )
    assert result["pending"] == 1
    case = container.recognition.list_cases(status="pending")[0]
    assert case["reason"] == "different_cities"
    assert case["cities"] == ["Bonn", "Köln"]

    rejected = container.recognition.decide(case["signature"], action="reject")
    assert rejected.body["decision"]["action"] == "reject"
    replay = container.recognition.decide(case["signature"], action="reject")
    assert replay.replayed
    again = container.recognition.synchronize(
        container.configuration.source_path,
        source_id="primary",
        minimum_year=2016,
    )
    assert again["pending"] == 0
    assert again["rejected"] == 1
    assert container.recognition.list_cases(status="rejected")[0]["signature"] == case["signature"]
    assert len(container.recognition.list_runs(limit=2)) == 2
    with pytest.raises(ValueError):
        container.recognition.list_cases(status="unknown")
    with pytest.raises(ValueError):
        container.recognition.list_runs(limit=0)
    with pytest.raises(ValueError):
        container.recognition.decide(case["signature"], action="unknown")
    with pytest.raises(ValueError):
        container.recognition.decide(
            case["signature"], action="assign", customer_id=1, expected_revision=1
        )


def test_accept_creates_customer_projects_and_is_idempotent(tmp_path: Path) -> None:
    container = _ambiguous_container(tmp_path)
    container.recognition.synchronize(
        container.configuration.source_path,
        source_id="primary",
        minimum_year=2016,
    )
    signature = container.recognition.list_cases()[0]["signature"]
    with pytest.raises(ValueError):
        container.recognition.decide(
            signature, action="accept", idempotency_key="recognition-accept-1"
        )
    with pytest.raises(ValueError):
        container.recognition.decide(
            signature,
            action="accept",
            customer_id=1,
            expected_revision=0,
            idempotency_key="recognition-accept-2",
        )
    accepted = container.recognition.decide(
        signature,
        action="accept",
        expected_revision=0,
        idempotency_key="recognition-accept-3",
    )
    customer = accepted.body["customer"]
    assert customer["display_name"] == "Alpha GmbH"
    assert len(customer["projects"]) == 2
    replay = container.recognition.decide(
        signature,
        action="accept",
        expected_revision=0,
        idempotency_key="recognition-accept-3",
    )
    assert replay.replayed
    same_decision = container.recognition.decide(
        signature,
        action="accept",
        expected_revision=0,
        idempotency_key="recognition-accept-4",
    )
    assert same_decision.body["customer"]["id"] == customer["id"]
    with pytest.raises(ValueError):
        container.recognition.decide(
            signature,
            action="reject",
            idempotency_key="recognition-conflicting-decision",
        )


def test_assign_requires_current_revision_and_persists_project_links(tmp_path: Path) -> None:
    container = _ambiguous_container(tmp_path)
    target = _seed_customer(container, city="Hamburg")
    container.recognition.synchronize(
        container.configuration.source_path,
        source_id="primary",
        minimum_year=2016,
    )
    signature = container.recognition.list_cases()[0]["signature"]
    with pytest.raises(ResourceNotFoundError):
        container.recognition.decide(
            signature,
            action="assign",
            customer_id=999,
            expected_revision=1,
            idempotency_key="recognition-assign-missing",
        )
    with pytest.raises(CustomerConflictError):
        container.recognition.decide(
            signature,
            action="assign",
            customer_id=target["id"],
            expected_revision=99,
            idempotency_key="recognition-assign-stale",
        )
    assigned = container.recognition.decide(
        signature,
        action="assign",
        customer_id=target["id"],
        expected_revision=target["revision"],
        idempotency_key="recognition-assign-current",
    )
    assert len(assigned.body["customer"]["projects"]) == 2
    replay = container.recognition.decide(
        signature,
        action="assign",
        customer_id=target["id"],
        expected_revision=target["revision"],
        idempotency_key="recognition-assign-current",
    )
    assert replay.replayed


def test_document_suggestion_review_protects_manual_values_and_rejections(tmp_path: Path) -> None:
    container = _ambiguous_container(tmp_path)
    customer = _seed_customer(container)
    factory = SqliteCustomerUnitOfWorkFactory(
        container.configuration.data_path / "customers.db"
    )
    with factory() as work:
        assert work.suggestions.add(
            customer["id"],
            kind="email",
            value="suggested@example.org",
            source_path="source://primary/Planung/2026/Alpha/Kontakt.txt",
            excerpt="Kontakt suggested@example.org",
            fingerprint="suggestion-email-1",
            confidence=0.9,
        )
        assert work.suggestions.add(
            customer["id"],
            kind="phone",
            value="0221 1234567",
            source_path="source://primary/Planung/2026/Alpha/Kontakt.txt",
            excerpt="Telefon 0221 1234567",
            fingerprint="suggestion-phone-1",
            confidence=0.75,
        )
        work.commit()
    suggestions, revision = container.suggestions.list_for_customer(customer["id"])
    by_field = {item["field_name"]: item for item in suggestions}

    rejected = container.suggestions.decide(
        customer["id"],
        by_field["phone"]["id"],
        action="reject",
        expected_revision=None,
        idempotency_key=None,
    )
    assert rejected.body["suggestion"]["status"] == "rejected"
    replay = container.suggestions.decide(
        customer["id"],
        by_field["phone"]["id"],
        action="reject",
        expected_revision=None,
        idempotency_key=None,
    )
    assert replay.replayed
    with pytest.raises(CustomerConflictError):
        container.suggestions.decide(
            customer["id"],
            by_field["email"]["id"],
            action="accept",
            expected_revision=revision + 1,
            idempotency_key="suggestion-email-stale",
        )
    accepted = container.suggestions.decide(
        customer["id"],
        by_field["email"]["id"],
        action="accept",
        expected_revision=revision,
        idempotency_key="suggestion-email-accept",
    )
    assert accepted.body["customer"]["email"] == "suggested@example.org"
    assert accepted.body["customer"]["revision"] == revision + 1
    assert container.suggestions.decide(
        customer["id"],
        by_field["email"]["id"],
        action="accept",
        expected_revision=revision,
        idempotency_key="suggestion-email-accept",
    ).replayed
    with pytest.raises(ValueError):
        container.suggestions.decide(
            customer["id"],
            by_field["email"]["id"],
            action="reject",
            expected_revision=None,
            idempotency_key="different-rejection",
        )

    with factory() as work:
        assert not work.suggestions.add(
            customer["id"],
            kind="phone",
            value="0221 1234567",
            source_path="source://primary/Planung/2026/Alpha/Kontakt.txt",
            excerpt="same",
            fingerprint="suggestion-phone-1",
            confidence=0.75,
        )
        with pytest.raises(ValueError):
            work.suggestions.add(
                customer["id"], kind="city", value="", source_path="x", excerpt="",
                fingerprint="bad-empty", confidence=0.5,
            )
        with pytest.raises(ValueError):
            work.suggestions.add(
                customer["id"], kind="unknown", value="x", source_path="x", excerpt="",
                fingerprint="bad-field", confidence=0.5,
            )
        with pytest.raises(ValueError):
            work.suggestions.add(
                customer["id"], kind="city", value="Köln", source_path="x", excerpt="",
                fingerprint="bad-confidence", confidence=True,
            )
        work.commit()
    with pytest.raises(ValueError):
        container.suggestions.list_for_customer(customer["id"], status="unknown")
    with pytest.raises(ResourceNotFoundError):
        container.suggestions.list_for_customer(999)


def test_accept_does_not_overwrite_manual_customer_value(tmp_path: Path) -> None:
    container = _ambiguous_container(tmp_path)
    customer = _seed_customer(container, email="manual@example.org")
    factory = SqliteCustomerUnitOfWorkFactory(
        container.configuration.data_path / "customers.db"
    )
    with factory() as work:
        work.suggestions.add(
            customer["id"], kind="email", value="other@example.org",
            source_path="source://primary/document.txt", excerpt="other@example.org",
            fingerprint="manual-conflict", confidence=0.9,
        )
        suggestion = work.suggestions.list_for_customer(customer["id"])[0]
        work.commit()
    with pytest.raises(SuggestionOverwriteError):
        container.suggestions.decide(
            customer["id"], suggestion["id"], action="accept",
            expected_revision=customer["revision"], idempotency_key="manual-conflict-key",
        )


def test_suggestion_decisions_use_client_auth_but_case_decisions_remain_admin(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        container = _ambiguous_container(tmp_path)
        customer = _seed_customer(container)
        factory = SqliteCustomerUnitOfWorkFactory(
            container.configuration.data_path / "customers.db"
        )
        with factory() as work:
            work.suggestions.add(
                customer["id"], kind="phone", value="0221 1234567",
                source_path="source://primary/document.txt", excerpt="0221 1234567",
                fingerprint="http-suggestion", confidence=0.75,
            )
            suggestion = work.suggestions.list_for_customer(customer["id"])[0]
            work.commit()
        app = create_app(container, manage_lifecycle=False)
        headers = {"Authorization": "Bearer recognition-client-token"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = f"/v2/customers/{customer['id']}/suggestions"
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers=headers)).status_code == 200
            decision = await client.post(
                f"{path}/{suggestion['id']}/decision",
                headers=headers,
                json={"action": "reject"},
            )
            assert decision.status_code == 200
            assert decision.json()["suggestion"]["status"] == "rejected"
            assert (
                await client.post(
                    "/v2/admin/recognition/cases/not-found/decision",
                    headers=headers,
                    json={"action": "reject"},
                )
            ).status_code == 403

    asyncio.run(scenario())


def test_migrated_contact_suggestion_can_be_accepted_without_overwrite(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    materialize_sqlite_fixture(
        "migration/customers-v0.4.1.sql", data / "customers.db"
    )
    source = tmp_path / "source"
    (source / "Beratung").mkdir(parents=True)
    container = build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=data,
            config_path=tmp_path / "config",
            client_token="recognition-client-token",
        )
    )
    suggestions, revision = container.suggestions.list_for_customer(1)
    contact = next(item for item in suggestions if item["suggestion_type"] == "contact")
    assert contact["contact"]["email"] == "max@beispiel.invalid"
    accepted = container.suggestions.decide(
        1,
        contact["id"],
        action="accept",
        expected_revision=revision,
        idempotency_key="accept-legacy-contact",
    )
    assert accepted.body["customer"]["revision"] == revision + 1
    assert {item["email"] for item in accepted.body["customer"]["contacts"]} == {
        "erika@beispiel.invalid",
        "max@beispiel.invalid",
    }


def test_recognition_assign_http_conflict_returns_current_customer(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        container = _ambiguous_container(tmp_path)
        target = _seed_customer(container, city="Hamburg")
        container.recognition.synchronize(
            container.configuration.source_path,
            source_id="primary",
            minimum_year=2016,
        )
        signature = container.recognition.list_cases()[0]["signature"]
        changed = container.customers.update_customer(
            target["id"],
            {"display_name": "Alpha GmbH", "city": "Hamburg"},
            expected_revision=target["revision"],
            idempotency_key="parallel-customer-change",
        ).body["customer"]
        app = create_app(container, manage_lifecycle=False)
        client_headers = {"Authorization": "Bearer recognition-client-token"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            login = await client.post(
                "/v2/admin/session",
                headers=client_headers,
                json={"password": "recognition-admin-password"},
            )
            admin = {
                **client_headers,
                "X-PapaGUI-Admin-Session": login.json()["token"],
                "Idempotency-Key": "parallel-recognition-assign",
            }
            endpoint = f"/v2/admin/recognition/cases/{signature}/decision"
            stale = await client.post(
                endpoint,
                headers=admin,
                json={
                    "action": "assign",
                    "customer_id": target["id"],
                    "expected_revision": target["revision"],
                },
            )
            assert stale.status_code == 409
            assert stale.json()["current"]["revision"] == changed["revision"]
            assigned = await client.post(
                endpoint,
                headers={**admin, "Idempotency-Key": "current-recognition-assign"},
                json={
                    "action": "assign",
                    "customer_id": target["id"],
                    "expected_revision": changed["revision"],
                },
            )
            assert assigned.status_code == 200
            assert assigned.json()["customer"]["projects"]

    asyncio.run(scenario())
