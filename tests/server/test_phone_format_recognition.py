"""Measurement rows must never become customer or contact phone suggestions."""

import json

import pytest

from papagui_server.adapters import customer_suggestions
from papagui_server.adapters.candidate_validation import valid_suggestion
from papagui_server.adapters.customer_suggestions import SqliteCustomerSuggestionRepository
from papagui_server.domain.customer_recognition import document_candidates
from tests.server.test_recognition_engine import CUSTOMER, fields
from tests.server.test_recognition_engine_persistence import store as store, customer, add


MEASUREMENTS = (
    "0.81 10.0 9.3 62.2 25.8",
    "0.67 10.0 9.3 50.4 24.8",
)


@pytest.mark.parametrize("raw", (
    *MEASUREMENTS, "030.12345678", "030 12.345.678", "0,81 10,0 9,3 62,2 25,8",
    "0.030 12345678", "0,030 12345678", "0 . 030 12345678",
    "030 12345678,00", "030 12345678.00", "030 12345678. 00",
))
@pytest.mark.parametrize("label", ("", "Telefon: ", "Tel.: "))
def test_decimal_measurements_and_salvaged_fragments_are_not_phones(raw, label):
    assert not fields(f"Kunde: {CUSTOMER}\n{label}{raw}")


@pytest.mark.parametrize("raw", (
    "040 12345678", "04123/4567890", "(040) 12345678", "040-12345678",
    "+49 40 12345678", "+49 (0)40 123456-12", "0049 40 12345678",
    "+44 20 7946 0958", "+1 202-555-0123", "040\u00a012345678",
    "+49 40 12345678 ext. 42", "+494012345678",
))
def test_common_domestic_and_international_formats_still_work(raw):
    candidate, = fields(f"Kunde: {CUSTOMER}\n{raw}")
    assert candidate.value == raw
    assert candidate.quality == "strong"
    assert valid_suggestion("phone", raw)


def test_compact_domestic_number_needs_a_local_phone_label():
    assert not fields(f"Kunde: {CUSTOMER}\n04012345678")
    candidate, = fields(f"Kunde: {CUSTOMER}\nTelefon: 04012345678")
    assert candidate.normalized_value == "+494012345678"
    assert not fields(f"Kunde: {CUSTOMER}\nTelefon: unbekannt; Referenz: 04012345678")


def test_repeated_measurements_do_not_gain_phone_evidence():
    blocks = [{"text": f"Kunde: {CUSTOMER}\n{MEASUREMENTS[i % 2]}", "page": i + 1}
              for i in range(12)]
    assert not fields("", blocks=blocks)


def test_invalid_measurements_do_not_hide_a_separate_phone_on_same_line():
    for text in (
        f"{MEASUREMENTS[0]}; Telefon: 040/12345678",
        f"Telefon: 040/12345678; {MEASUREMENTS[1]}",
        "Telefon: 040/12345678, +44 20 7946 0958",
    ):
        expected = {"+494012345678"}
        if "+44" in text:
            expected.add("+442079460958")
        assert {item.normalized_value for item in fields(f"Kunde: {CUSTOMER}\n{text}")} == expected


def test_contact_keeps_email_without_measurement_phone():
    text = f"Kunde: {CUSTOMER}\nAnsprechpartner: Mira Muster\nTelefon: {MEASUREMENTS[0]}"
    assert not document_candidates(text, customer_name=CUSTOMER)
    candidate, = document_candidates(text + "\nE-Mail: mira@example.org", customer_name=CUSTOMER)
    assert candidate.field_name == "contact"
    assert candidate.payload["phone"] == ""
    assert candidate.payload["email"] == "mira@example.org"


@pytest.mark.parametrize("kind", ("phone", "contact"))
def test_repository_rejects_new_measurement_suggestions(store, kind):
    _, repo = store
    payload = {"name": "Mira Muster", "email": "mira@example.org", "phone": MEASUREMENTS[0]}
    assert not add(repo, kind=kind, value=json.dumps(payload) if kind == "contact" else MEASUREMENTS[0],
                   payload=payload if kind == "contact" else None)
    assert not repo.list_for_customer(1, status="pending")


def seed_previous_version(connection, repo, monkeypatch, *, value, kind="phone", payload=None):
    # Reproduce an old detector's persisted output, before format validation.
    with monkeypatch.context() as previous:
        previous.setattr(customer_suggestions, "valid_suggestion", lambda *args: True)
        assert add(repo, kind=kind, value=value, payload=payload)
    connection.execute(
        "UPDATE candidate_schema_metadata SET value='1' WHERE key='candidate_validation_version'",
    )


@pytest.mark.parametrize("kind", ("phone", "contact"))
def test_upgrade_hides_old_measurements_without_rechecking_documents(store, monkeypatch, kind):
    connection, repo = store
    payload = {"name": "Mira Muster", "email": "mira@example.org", "phone": MEASUREMENTS[0]}
    seed_previous_version(connection, repo, monkeypatch, kind=kind,
                          value=json.dumps(payload) if kind == "contact" else MEASUREMENTS[0],
                          payload=payload if kind == "contact" else None)
    before = customer(connection)
    upgraded = SqliteCustomerSuggestionRepository(connection)
    assert not upgraded.list_for_customer(1, status="pending")
    assert customer(connection) == before
    assert connection.execute("SELECT lifecycle FROM customer_document_suggestions").fetchone()[0] == "invalid"
    assert connection.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM candidate_evidence WHERE active=1").fetchone()[0] == 0
    # Opening the repository again does not resurrect or repeat the cleanup.
    assert not SqliteCustomerSuggestionRepository(connection).list_for_customer(1, status="pending")


@pytest.mark.parametrize("action", ("accept", "reject"))
def test_upgrade_preserves_confirmed_values_and_decision_history(store, monkeypatch, action):
    connection, repo = store
    seed_previous_version(connection, repo, monkeypatch, value=MEASUREMENTS[0])
    suggestion, = repo.list_for_customer(1, status="pending")
    with monkeypatch.context() as previous:
        previous.setattr(customer_suggestions, "valid_suggestion", lambda *args: True)
        repo.decide(1, suggestion["id"], action=action, expected_revision=1, current_customer=customer(connection))
    before = customer(connection)
    decisions = [tuple(row) for row in connection.execute("SELECT * FROM candidate_decisions")]
    SqliteCustomerSuggestionRepository(connection)
    assert customer(connection) == before
    assert [tuple(row) for row in connection.execute("SELECT * FROM candidate_decisions")] == decisions
    assert connection.execute("SELECT status FROM customer_document_suggestions").fetchone()[0] == (
        "accepted" if action == "accept" else "rejected"
    )


def test_new_valid_evidence_replaces_invalid_display_spelling_under_same_id(store, monkeypatch):
    connection, repo = store
    seed_previous_version(connection, repo, monkeypatch, value="030.12345678")
    old, = repo.list_for_customer(1, status="pending")
    upgraded = SqliteCustomerSuggestionRepository(connection)
    assert not upgraded.list_for_customer(1, status="pending")
    add(upgraded, value="030/12345678", source="source://synthetic/new-letter.txt", run="2")
    new, = upgraded.list_for_customer(1, status="pending")
    assert new["id"] == old["id"]
    assert new["value"] == "030/12345678"
    assert new["evidence_count"] == 1
    assert valid_suggestion("phone", new["value"])
    assert customer(connection)["phone"] == ""
