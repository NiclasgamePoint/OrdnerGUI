"""Global exclusions tested only with invented in-memory recognition evidence."""

from __future__ import annotations

import json
import socket
import sqlite3
from unittest.mock import Mock, call

import pytest

from papagui_server.adapters.customer_schema import initialize_customer_schema
from papagui_server.adapters.customer_repository import SqliteCustomerRepository
from papagui_server.adapters.customer_suggestions import SqliteCustomerSuggestionRepository
from papagui_server.adapters.recognition_blocklist import SqliteRecognitionBlocklistRepository
from papagui_server.application.recognition_blocklist import RecognitionBlocklistApplicationService
from papagui_server.domain.errors import ResourceNotFoundError


@pytest.fixture
def store():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    initialize_customer_schema(connection)
    connection.executemany(
        "INSERT INTO customers(id,folder_path,display_name) VALUES (?,?,?)",
        [(1, "customer://synthetic-one", "Synthetic One"),
         (2, "customer://synthetic-two", "Synthetic Two")],
    )
    suggestions = SqliteCustomerSuggestionRepository(connection)
    yield connection, suggestions, SqliteRecognitionBlocklistRepository(connection)
    connection.close()


def propose(suggestions, *, customer_id=1, kind="phone", value="030 12345678", payload=None):
    return suggestions.add(
        customer_id, kind=kind, value=value, payload=payload,
        suggestion_type="contact" if kind == "contact" else "field",
        source_path="source://synthetic/evidence.txt", excerpt="Invented document evidence",
        fingerprint="", confidence=0, quality="strong", party_role="customer",
        engine_version="synthetic", run_id="one",
    )


def lifecycle(connection, candidate_id):
    return connection.execute(
        "SELECT lifecycle FROM customer_document_suggestions WHERE id=?", (candidate_id,)
    ).fetchone()[0]


def test_exact_phone_exclusion_is_normalized_global_and_retains_evidence(store):
    connection, suggestions, blocklist = store
    for customer_id, value in ((1, "030 12345678"), (2, "+49 30 12345678")):
        propose(suggestions, customer_id=customer_id, value=value)
    propose(suggestions, value="030 12345678 ext. 42")
    before = [tuple(row) for row in connection.execute("SELECT * FROM candidate_evidence")]
    entry = blocklist.add("phone", "0049 30 12345678", "Invented shared switchboard")
    assert entry["normalized_value"] == "+493012345678"
    assert entry["reason"] == "Invented shared switchboard"
    remaining, = suggestions.list_for_customer(1, status="pending")
    assert remaining["value"] == "030 12345678 ext. 42"
    assert suggestions.list_for_customer(2, status="pending") == []
    assert [tuple(row) for row in connection.execute("SELECT * FROM candidate_evidence")] == before
    assert blocklist.matches("phone", "+49 (30) 12345678")
    assert not blocklist.matches("phone", "+49 30 12345678 ext. 42")


def test_duplicate_normalized_entries_keep_stable_id_reason_and_created_time(store):
    _, _, blocklist = store
    first = blocklist.add("company", "  Synthetic   GmbH ", "Original reason")
    duplicate = blocklist.add("company", "SYNTHETIC GmbH", "Second reason")
    assert duplicate == first
    assert blocklist.list() == [first]
    assert blocklist.matches("company", "synthetic gmbh")
    assert not blocklist.matches("company", "synthetic gmbh services")
    assert not blocklist.matches("contact_name", "synthetic gmbh")


def test_new_matching_evidence_is_retained_hidden_and_returns_after_removal(store):
    connection, suggestions, blocklist = store
    entry = blocklist.add("phone", "030 12345678")
    assert not propose(suggestions, customer_id=2, value="+49 30 12345678")
    assert suggestions.list_for_customer(2, status="pending") == []
    row, = connection.execute("SELECT id,status,lifecycle FROM customer_document_suggestions").fetchall()
    assert row["status"] == "pending" and row["lifecycle"] == "blocked"
    assert connection.execute("SELECT count(*) FROM candidate_evidence WHERE active=1").fetchone()[0] == 1
    blocklist.delete(entry["id"])
    restored, = suggestions.list_for_customer(2, status="pending")
    assert restored["id"] == row["id"]


@pytest.mark.parametrize("kind,value,component", [
    ("phone", "0049 30 12345678", {"phone": "030 12345678"}),
    ("email", "mira@example.ORG", {"email": "mira@example.org"}),
    ("email_domain", "EXAMPLE.ORG", {"email": "mira@example.org"}),
    ("contact_name", "  MIRA   Muster ", {"name": "Mira Muster"}),
])
def test_grouped_contacts_are_excluded_by_individual_components(store, kind, value, component):
    _, suggestions, blocklist = store
    payload = {"name": "Synthetic Contact", "phone": "", "email": "", "role": "Planning", **component}
    propose(suggestions, kind="contact", value=json.dumps(payload), payload=payload)
    entry = blocklist.add(kind, value)
    assert suggestions.list_for_customer(1, status="pending") == []
    assert blocklist.matches("contact", json.dumps(payload))
    assert blocklist.matches("contact", "", payload)
    assert not propose(suggestions, customer_id=2, kind="contact", value=json.dumps(payload), payload=payload)
    assert suggestions.list_for_customer(2, status="pending") == []
    blocklist.delete(entry["id"])
    restored, = suggestions.list_for_customer(1, status="pending")
    assert restored["contact"]["name"] == payload["name"]
    assert len(suggestions.list_for_customer(2, status="pending")) == 1


def test_domain_validation_and_idna_matching_are_offline_and_exact(store, monkeypatch):
    _, _, blocklist = store

    def forbid_network(*args, **kwargs):
        raise AssertionError("Recognition blocklist validation must stay offline")

    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    entry = blocklist.add("email_domain", "BÜCHER.DE")
    assert entry["normalized_value"] == "xn--bcher-kva.de"
    assert blocklist.matches("email", "person@xn--bcher-kva.de")
    assert blocklist.matches("email", "person@bücher.de")
    assert not blocklist.matches("email", "person@shop.bücher.de")
    assert not blocklist.matches("email", "person@other-bücher.de")
    sharp_s = blocklist.add("email_domain", "faß.de")
    assert sharp_s["normalized_value"] == "xn--fa-hia.de"
    assert not blocklist.matches("email", "person@fass.de")


def test_email_normalization_retains_plus_tags_and_distinct_local_parts(store):
    _, _, blocklist = store
    blocklist.add("email", "Person+design@example.ORG")
    assert blocklist.matches("email", "Person+design@example.org")
    assert not blocklist.matches("email", "Person@example.org")
    assert not blocklist.matches("email", "person+design@example.org")
    assert not blocklist.matches("email", "Person+other@example.org")


@pytest.mark.parametrize("kind,value", [
    ("email_domain", "*"), ("email_domain", "*.example.org"),
    ("email_domain", "example.*"), ("email_domain", "org"),
    ("email_domain", "https://example.org"), ("email_domain", "@example.org"),
    ("email_domain", "example.org/path"), ("email_domain", "example..org"),
    ("phone", "2026-01-12"), ("phone", "123"), ("email", "not-an-email"),
    ("contact_name", "  "), ("company", ""), ("anything", "example.org"),
])
def test_invalid_values_do_not_create_exclusions(store, kind, value):
    _, _, blocklist = store
    with pytest.raises(ValueError):
        blocklist.add(kind, value)
    assert blocklist.list() == []


@pytest.mark.parametrize("value,reason", [("x" * 321, ""), ("Synthetic GmbH", "x" * 501)])
def test_repository_limits_match_the_api_contract(store, value, reason):
    _, _, blocklist = store
    with pytest.raises(ValueError):
        blocklist.add("company", value, reason)
    assert blocklist.list() == []


def test_decisions_manual_fields_contacts_and_provenance_remain_unchanged(store):
    connection, suggestions, blocklist = store
    for customer_id, action in ((1, "accept"), (2, "reject")):
        propose(suggestions, customer_id=customer_id)
        item, = suggestions.list_for_customer(customer_id, status="pending")
        customer = dict(connection.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone())
        suggestions.decide(customer_id, item["id"], action=action, expected_revision=1,
                           current_customer={**customer, "contacts": []}, reason="other")
    connection.execute("UPDATE customers SET company='Manual Synthetic Company',city='Bonn' WHERE id=1")
    connection.execute(
        "INSERT INTO contacts(customer_id,uid,name,phone) VALUES (1,'synthetic-contact','Manual Person','030 12345678')"
    )
    tables = ("customers", "contacts", "customer_field_provenance", "candidate_decisions",
              "candidate_evidence", "customer_document_suggestions")
    before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
              for table in tables}
    entry = blocklist.add("phone", "030 12345678")
    blocklist.delete(entry["id"])
    after = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
             for table in tables}
    assert after == before
    assert suggestions.list_for_customer(1, status="accepted")
    assert suggestions.list_for_customer(2, status="rejected")


def test_removal_only_restores_blocked_candidates_with_active_evidence(store):
    connection, suggestions, blocklist = store
    propose(suggestions)
    active, = suggestions.list_for_customer(1, status="pending")
    propose(suggestions, customer_id=2)
    obsolete, = suggestions.list_for_customer(2, status="pending")
    entry = blocklist.add("phone", "030 12345678")
    connection.execute("UPDATE candidate_evidence SET active=0 WHERE candidate_id=?", (obsolete["id"],))
    blocklist.delete(entry["id"])
    assert lifecycle(connection, active["id"]) == "active"
    assert lifecycle(connection, obsolete["id"]) == "stale"
    assert suggestions.list_for_customer(2, status="pending") == []


def test_removing_one_exclusion_keeps_other_matching_rules_effective(store):
    _, suggestions, blocklist = store
    propose(suggestions, kind="email", value="person@example.org")
    address = blocklist.add("email", "person@example.org")
    domain = blocklist.add("email_domain", "example.org")
    blocklist.delete(address["id"])
    assert suggestions.list_for_customer(1, status="pending") == []
    blocklist.delete(domain["id"])
    assert len(suggestions.list_for_customer(1, status="pending")) == 1


@pytest.mark.parametrize("state", ["stale", "invalid", "superseded"])
def test_unrelated_hidden_lifecycles_are_never_reactivated(store, state):
    connection, suggestions, blocklist = store
    propose(suggestions)
    item, = suggestions.list_for_customer(1, status="pending")
    connection.execute("UPDATE customer_document_suggestions SET lifecycle=? WHERE id=?", (state, item["id"]))
    entry = blocklist.add("phone", "030 12345678")
    blocklist.delete(entry["id"])
    assert lifecycle(connection, item["id"]) == state


def test_schema_upgrade_from_version_three_is_additive_and_idempotent(store):
    connection, suggestions, _ = store
    propose(suggestions)
    before = [tuple(row) for row in connection.execute("SELECT * FROM customer_document_suggestions")]
    connection.execute("DROP TABLE recognition_blocklist")
    connection.execute("UPDATE candidate_schema_metadata SET value='3' WHERE key='storage_layout_version'")
    connection.commit()
    initialize_customer_schema(connection)
    initialize_customer_schema(connection)
    assert connection.execute(
        "SELECT value FROM candidate_schema_metadata WHERE key='storage_layout_version'"
    ).fetchone()[0] == "4"
    assert [tuple(row) for row in connection.execute("SELECT * FROM customer_document_suggestions")] == before
    assert SqliteRecognitionBlocklistRepository(connection).list() == []
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_exclusions_persist_in_the_database_across_reopen(tmp_path):
    path = tmp_path / "synthetic-blocklist.sqlite"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        initialize_customer_schema(connection)
        entry = SqliteRecognitionBlocklistRepository(connection).add("company", "Synthetic GmbH")
    with sqlite3.connect(path) as reopened:
        reopened.row_factory = sqlite3.Row
        initialize_customer_schema(reopened)
        assert SqliteRecognitionBlocklistRepository(reopened).list() == [entry]


def test_service_commits_before_snapshot_publication_and_reports_missing_ids(store):
    connection, _, blocklist = store
    events = []

    class Work:
        def __init__(self):
            self.blocklist = blocklist

        def __enter__(self):
            return self

        def __exit__(self, exc_type, *_):
            if exc_type is not None:
                connection.rollback()

        def commit(self):
            connection.commit()
            events.append("commit")

    class Publisher:
        def publish_customers(self):
            assert not connection.in_transaction
            events.append("publish")

    service = RecognitionBlocklistApplicationService(Work, Publisher())
    entry = service.add("email", "synthetic@example.org")
    assert service.list() == [entry]
    service.delete(entry["id"])
    assert service.list() == []
    assert events == ["commit", "publish"] * 2
    with pytest.raises(ResourceNotFoundError):
        service.delete(entry["id"])
    assert events == ["commit", "publish"] * 2


def test_unblock_reconciles_only_restored_customers_before_offline_snapshot_publication(store, monkeypatch):
    from papagui_client.adapters.sqlite_suggestion_snapshot import read_suggestion_page

    connection, suggestions, blocklist = store
    customers = SqliteCustomerRepository(connection, suggestions=suggestions)
    contact = {"name": "Synthetic Person", "role": "Planning", "email": "person@example.org",
               "phone": "030 12345678"}
    propose(suggestions)
    propose(suggestions, kind="contact", value=json.dumps(contact), payload=contact)
    propose(suggestions, customer_id=2, kind="email", value="unrelated@example.org")
    blocked = blocklist.add("phone", "+49 30 12345678")
    customers.update(1, {"phone": "+49 30 12345678", "contacts": [contact]}, expected_revision=1)
    connection.commit()
    protected_tables = ("customers", "contacts", "customer_field_provenance", "candidate_decisions")
    before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
              for table in protected_tables}
    get = Mock(wraps=customers.get)
    listing = Mock(side_effect=AssertionError("Unblocking must not scan every customer"))
    monkeypatch.setattr(customers, "get", get)
    monkeypatch.setattr(customers, "list", listing)
    published = []

    class Work:
        def __init__(self):
            self.customers, self.suggestions, self.blocklist = customers, suggestions, blocklist

        def __enter__(self):
            return self

        def __exit__(self, exc_type, *_):
            if exc_type is not None:
                connection.rollback()

        def commit(self):
            connection.commit()

    class Publisher:
        def publish_customers(self):
            assert not connection.in_transaction
            with sqlite3.connect(":memory:") as snapshot:
                snapshot.row_factory = sqlite3.Row
                connection.backup(snapshot)
                published.append(read_suggestion_page(snapshot, 1, "pending", 30, 0))

    service = RecognitionBlocklistApplicationService(Work, Publisher())
    assert service.delete(blocked["id"])
    assert get.call_args_list == [call(1)]
    listing.assert_not_called()
    assert len(published) == 1 and published[0].total == 0 and published[0].offline
    assert blocklist.restored_customer_ids == (1,)
    assert suggestions.count_for_customer(2, status="pending") == 1
    assert [row[0] for row in connection.execute(
        "SELECT lifecycle FROM customer_document_suggestions WHERE customer_id=1"
    )] == ["satisfied", "satisfied"]
    after = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
             for table in protected_tables}
    assert after == before
