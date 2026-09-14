"""Independent review regressions against invented in-memory customer records."""
from __future__ import annotations

import json
import sqlite3

import pytest

from papagui_server.adapters.customer_schema import initialize_customer_schema
from papagui_server.adapters.customer_suggestions import SqliteCustomerSuggestionRepository
from papagui_server.adapters.candidate_migration import canonical_value
from papagui_server.domain.errors import SuggestionOverwriteError


@pytest.fixture
def store():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    initialize_customer_schema(connection)
    connection.execute("INSERT INTO customers(id,folder_path,display_name) VALUES (1,'customer://synthetic-review','Review Beispiel')")
    repo = SqliteCustomerSuggestionRepository(connection)
    yield connection, repo
    connection.close()


def customer(connection):
    return {**dict(connection.execute("SELECT * FROM customers WHERE id=1").fetchone()), "contacts": []}


def add(repo, *, source="source://synthetic/one.txt", excerpt="invented excerpt", value="030 12345678", kind="phone", payload=None, run="1", quality="strong"):
    return repo.add(1, kind=kind, value=value, source_path=source, excerpt=excerpt,
                    fingerprint=source + value, confidence=0, quality=quality,
                    suggestion_type="address" if kind == "address" else "field", payload=payload,
                    engine_version="synthetic-test", party_role="customer", run_id=run)


def test_active_source_and_excerpt_always_refer_to_same_evidence(store):
    connection, repo = store
    add(repo, source="source://synthetic/removed.txt", excerpt="Removed source excerpt")
    add(repo, source="source://synthetic/active.txt", excerpt="Active source excerpt", run="2")
    repo.finalize_run(1, "2", evaluated_sources={"source://synthetic/active.txt"}, existing_sources={"source://synthetic/active.txt"})
    item, = repo.list_for_customer(1, status="pending")
    assert item["source"]["relative_path"] == "active.txt"
    assert item["excerpt"] == "Active source excerpt"
    assert item["evidence_count"] == 1


@pytest.mark.parametrize("kind", ["phone", "contact"])
def test_bounded_recheck_retires_excel_phone_evidence_outside_document_budget(store, kind):
    connection, repo = store
    source = "source://synthetic/contacts.XLSX"
    payload = {"name": "Mira Muster", "email": "mira@example.org", "phone": "030 12345678"}
    repo.add(
        1, kind=kind, value=json.dumps(payload) if kind == "contact" else "030 12345678",
        payload=payload if kind == "contact" else None,
        suggestion_type="contact" if kind == "contact" else "field",
        source_path=source, excerpt="Synthetic Excel evidence", fingerprint="old-excel",
        confidence=0, quality="strong", party_role="customer", run_id="old",
    )
    add(repo, source=source, kind="email", value="office@example.org")
    add(repo, source="source://synthetic/letter.pdf", value="+44 20 7946 0958")
    repo.finalize_run(1, "new", evaluated_sources=set())
    pending = repo.list_for_customer(1, status="pending")
    assert {(item["field_name"], item["value"]) for item in pending} == {
        ("email", "office@example.org"), ("phone", "+44 20 7946 0958"),
    }
    assert customer(connection)["phone"] == ""
    assert connection.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 0


def test_rejection_survives_phone_format_copy_and_rename(store):
    connection, repo = store
    add(repo)
    suggestion, = repo.list_for_customer(1, status="pending")
    repo.decide(1, suggestion["id"], action="reject", reason="outdated", expected_revision=1, current_customer=customer(connection))
    assert not add(repo, source="source://synthetic/renamed.txt", value="+49 30 12345678", run="2")
    assert repo.list_for_customer(1, status="pending") == []
    assert connection.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM candidate_evidence WHERE active=1").fetchone()[0] == 2
    assert customer(connection)["phone"] == ""


def test_address_classification_metadata_is_not_part_of_the_value_key():
    values = {"street": "Postfach 12", "postal_code": "10115", "city": "Berlin"}
    assert canonical_value("address", json.dumps(values), values) == canonical_value("address", json.dumps(values), {**values, "address_kind": "postbox"})


def test_partial_address_acceptance_never_silently_combines_existing_city(store):
    connection, repo = store
    connection.execute("UPDATE customers SET city='Bonn' WHERE id=1")
    payload = {"street": "Musterstraße 12", "postal_code": "", "city": "", "address_kind": "incomplete"}
    add(repo, kind="address", value=json.dumps(payload), payload=payload, quality="review")
    item, = repo.list_for_customer(1, status="pending")
    with pytest.raises((ValueError, SuggestionOverwriteError)):
        repo.decide(1, item["id"], action="accept", expected_revision=1, current_customer=customer(connection))
    assert customer(connection)["street"] == ""
    assert customer(connection)["city"] == "Bonn"
    assert customer(connection)["revision"] == 1
    assert repo.list_for_customer(1, status="pending")


def test_complete_postbox_can_be_explicitly_accepted_as_one_review_group(store):
    connection, repo = store
    payload = {"street": "Postfach 12", "postal_code": "10115", "city": "Berlin", "address_kind": "postbox"}
    add(repo, kind="address", value=json.dumps(payload), payload=payload, quality="review")
    item, = repo.list_for_customer(1, status="pending")
    repo.decide(1, item["id"], action="accept", expected_revision=1, current_customer=customer(connection))
    assert customer(connection)["street"] == "Postfach 12"
    assert customer(connection)["postal_code"] == "10115"
    assert customer(connection)["city"] == "Berlin"
    assert customer(connection)["revision"] == 2
    assert connection.execute("SELECT count(*) FROM customer_field_provenance WHERE origin='confirmed'").fetchone()[0] == 3


def test_identical_rejected_value_can_be_proposed_for_another_customer(store):
    connection, repo = store
    add(repo)
    suggestion, = repo.list_for_customer(1, status="pending")
    repo.decide(1, suggestion["id"], action="reject", expected_revision=1, current_customer=customer(connection), reason="wrong_customer")
    connection.execute("INSERT INTO customers(id,folder_path,display_name) VALUES (2,'customer://synthetic-second','Anderer Testkunde')")
    assert repo.add(2, kind="phone", value="030 12345678", source_path="source://synthetic/moved.txt", excerpt="Other customer evidence", fingerprint="moved-evidence", confidence=0, quality="strong", engine_version="synthetic-test")
    assert len(repo.list_for_customer(2, status="pending")) == 1
    assert repo.list_for_customer(1, status="pending") == []


def test_quality_and_reasons_are_aggregated_from_active_evidence(store):
    connection, repo = store
    kwargs = dict(kind="phone",value="030 12345678",confidence=0,party_key="customer",party_role="customer",engine_version="test")
    repo.add(1,source_path="source://synthetic/strong.txt",excerpt="Strong source",fingerprint="strong",quality="strong",reasons=("identified-customer",),run_id="1",**kwargs)
    repo.add(1,source_path="source://synthetic/weak.txt",excerpt="Weak source",fingerprint="weak",quality="review",reasons=("ocr-quality-unverified",),run_id="2",**kwargs)
    item, = repo.list_for_customer(1,status="pending")
    assert item["quality"] == "strong"
    assert item["reasons"] == ["identified-customer"]
    assert item["excerpt"] == "Strong source"
    repo.finalize_run(1,"2",evaluated_sources={"source://synthetic/weak.txt"},existing_sources={"source://synthetic/weak.txt"})
    item, = repo.list_for_customer(1,status="pending")
    assert item["quality"] == "review"
    assert item["reasons"] == ["ocr-quality-unverified"]
    assert item["excerpt"] == "Weak source"


def test_no_text_reassessment_removes_pending_evidence_but_keeps_accepted_value(store):
    connection, repo = store
    add(repo)
    item, = repo.list_for_customer(1,status="pending")
    repo.decide(1,item["id"],action="accept",expected_revision=1,current_customer=customer(connection))
    add(repo,kind="email",value="new@example.org")
    repo.finalize_run(1,"2",evaluated_sources={"source://synthetic/one.txt"},existing_sources={"source://synthetic/one.txt"})
    assert repo.list_for_customer(1,status="pending") == []
    assert customer(connection)["phone"] == "030 12345678"
    accepted, = repo.list_for_customer(1,status="accepted")
    assert accepted["evidence_count"] == 0
    assert "no-active-evidence" in accepted["reasons"]


def contact_customer(connection):
    return {**customer(connection), "contacts":[dict(row) for row in connection.execute("SELECT uid AS id,name,role,email,phone FROM contacts WHERE customer_id=1")]}


def contact_add(repo, payload):
    repo.add(1,kind="contact",value=json.dumps(payload),source_path="source://synthetic/contact.txt",excerpt="Contact source",fingerprint="contact-fingerprint",confidence=0,quality="strong",suggestion_type="contact",payload=payload,party_key="customer:contact:synthetic",party_role="customer",engine_version="test")


def test_same_named_contact_different_phone_is_conflict_not_duplicate(store):
    connection, repo = store
    connection.execute("INSERT INTO contacts(customer_id,uid,name,email,phone) VALUES (1,'synthetic-contact','Mira Muster','mira@example.org','030 12345678')")
    contact_add(repo,{"name":"Mira Muster","role":"","email":"mira@example.org","phone":"+44 20 7946 0958"})
    item, = repo.list_for_customer(1,status="pending")
    assert item["is_conflict"] is True
    with pytest.raises(SuggestionOverwriteError):
        repo.decide(1,item["id"],action="accept",expected_revision=1,current_customer=contact_customer(connection))
    assert connection.execute("SELECT count(*) FROM contacts").fetchone()[0] == 1
    assert contact_customer(connection)["contacts"][0]["phone"] == "030 12345678"
    assert customer(connection)["revision"] == 1


def test_same_named_contact_missing_phone_is_completed_under_stable_id(store):
    connection, repo = store
    connection.execute("INSERT INTO contacts(customer_id,uid,name,email) VALUES (1,'synthetic-contact','Mira Muster','mira@example.org')")
    contact_add(repo,{"name":"Mira Muster","role":"","email":"mira@example.org","phone":"030 12345678"})
    item, = repo.list_for_customer(1,status="pending")
    assert item["is_conflict"] is False
    repo.decide(1,item["id"],action="accept",expected_revision=1,current_customer=contact_customer(connection))
    contacts = contact_customer(connection)["contacts"]
    assert len(contacts) == 1 and contacts[0]["id"] == "synthetic-contact"
    assert contacts[0]["phone"] == "030 12345678"
    assert customer(connection)["revision"] == 2
    provenance = connection.execute("SELECT field_name,target_id FROM customer_field_provenance WHERE origin='confirmed'").fetchall()
    assert [tuple(row) for row in provenance] == [("phone","synthetic-contact")]


def test_ambiguous_same_named_contacts_require_review_instead_of_guessing(store):
    connection, repo = store
    connection.executemany("INSERT INTO contacts(customer_id,uid,name,email) VALUES (1,?,'Mira Muster',?)", [("contact-1","one@example.org"),("contact-2","two@example.org")])
    contact_add(repo,{"name":"Mira Muster","role":"","email":"","phone":"030 12345678"})
    item, = repo.list_for_customer(1,status="pending")
    assert item["is_conflict"] is True
    with pytest.raises(SuggestionOverwriteError):
        repo.decide(1,item["id"],action="accept",expected_revision=1,current_customer=contact_customer(connection))
    assert connection.execute("SELECT count(*) FROM contacts").fetchone()[0] == 2


def test_legacy_rejection_scope_survives_more_than_one_party_reassessment(store):
    connection, repo = store
    connection.execute("DELETE FROM candidate_schema_metadata WHERE key='canonical_version'")
    connection.execute("INSERT INTO customer_document_suggestions(customer_id,kind,value,source_path,fingerprint,confidence,status) VALUES (1,'phone','030 12345678','source://synthetic/old.txt','old',0,'rejected')")
    repo = SqliteCustomerSuggestionRepository(connection)
    for party in ("unresolved:one","unresolved:two","customer"):
        assert not repo.add(1,kind="phone",value="+49 30 12345678",source_path="source://synthetic/new.txt",excerpt="Reassessed source",fingerprint="",confidence=0,quality="review",party_key=party,engine_version="new-engine")
    assert repo.list_for_customer(1,status="pending") == []
    assert connection.execute("SELECT count(*) FROM customer_document_suggestions").fetchone()[0] == 1


def test_conflicting_legacy_decisions_keep_conflict_marker_after_new_evidence(store):
    connection, repo = store
    connection.execute("DELETE FROM candidate_schema_metadata WHERE key='canonical_version'")
    connection.executemany("INSERT INTO customer_document_suggestions(customer_id,kind,value,source_path,fingerprint,confidence,status) VALUES (1,'phone',?,'source://synthetic/old.txt',?,0,?)", [("030 12345678","old-accepted","accepted"),("+49 30 12345678","old-rejected","rejected")])
    repo = SqliteCustomerSuggestionRepository(connection)
    assert repo.recognition_status(1)["counts"]["migration_conflicts"] == 1
    add(repo)
    item, = repo.list_for_customer(1,status="accepted")
    assert item["quality"] == "review"
    assert "legacy_decision_conflict" in item["reasons"]
    assert connection.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 2
    assert repo.recognition_status(1)["counts"]["migration_conflicts"] == 1


def test_new_customer_scoped_rejection_survives_party_and_rule_changes(store):
    connection, repo = store
    shared = dict(kind="phone",value="030 12345678",source_path="source://synthetic/same.txt",excerpt="Invented source",fingerprint="",confidence=0,engine_version="test")
    repo.add(1,party_key="unresolved:one",quality="review",**shared)
    original, = repo.list_for_customer(1,status="pending")
    repo.decide(1,original["id"],action="reject",expected_revision=1,current_customer=customer(connection),reason="wrong_customer")
    for party,quality,role in (("unresolved:two","review","unknown"),("customer","strong","customer")):
        assert not repo.add(1,party_key=party,quality=quality,party_role=role,**shared)
    rejected, = repo.list_for_customer(1,status="rejected")
    assert rejected["id"] == original["id"]
    assert repo.list_for_customer(1,status="pending") == []


def test_party_resolution_keeps_pending_id_only_with_matching_evidence(store):
    connection, repo = store
    shared = dict(kind="phone",value="030 12345678",source_path="source://synthetic/same.txt",source_locator={"page":2,"block_id":"A"},excerpt="Invented source",fingerprint="",confidence=0,engine_version="test")
    repo.add(1,party_key="unresolved:one",quality="review",**shared)
    original, = repo.list_for_customer(1,status="pending")
    assert not repo.add(1,party_key="customer",party_role="customer",quality="strong",**shared)
    resolved, = repo.list_for_customer(1,status="pending")
    assert resolved["id"] == original["id"] and resolved["quality"] == "strong"
    assert connection.execute("SELECT count(*) FROM candidate_aliases WHERE candidate_id=?",(original["id"],)).fetchone()[0] == 2
    separate = {**shared,"source_locator":{"page":3,"block_id":"B"}}
    assert repo.add(1,party_key="unresolved:two",quality="review",**separate)
    assert repo.count_for_customer(1,status="pending") == 2


def test_exact_text_family_counts_exports_once_without_merging_people(store):
    connection, repo = store
    shared = dict(kind="phone",value="030 12345678",excerpt="Identical synthetic document text",fingerprint="",confidence=0,document_family="normalized-text-digest",quality="strong")
    repo.add(1,source_path="source://synthetic/copy.pdf",document_hash="pdf-binary-digest",**shared)
    repo.add(1,source_path="source://synthetic/copy.docx",document_hash="docx-binary-digest",**shared)
    phone, = repo.list_for_customer(1,status="pending")
    assert phone["evidence_count"] == 1 and len(phone["evidence"]) == 2
    for name,party in (("Mira Muster","mira"),("Toni Muster","toni")):
        payload={"name":name,"role":"","email":"","phone":"030 12345678"}
        repo.add(1,kind="contact",value=json.dumps(payload),payload=payload,suggestion_type="contact",party_key="customer:contact:"+party,source_path="source://synthetic/contact.pdf",excerpt="Shared switchboard",fingerprint="",confidence=0,document_family="same-contactlist-digest",quality="strong")
    contacts = [item for item in repo.list_for_customer(1,status="pending") if item["suggestion_type"]=="contact"]
    assert len(contacts)==2 and len({item["id"] for item in contacts})==2


def test_customer_value_rejection_immediately_resolves_existing_duplicate_cards(store):
    connection, repo = store
    shared=dict(kind="phone",value="030 12345678",excerpt="Invented source",fingerprint="",confidence=0,quality="review",engine_version="test")
    repo.add(1,party_key="unresolved:one",source_path="source://synthetic/one.txt",**shared)
    repo.add(1,party_key="unresolved:two",source_path="source://synthetic/two.txt",**shared)
    first,second=repo.list_for_customer(1,status="pending")
    repo.decide(1,first["id"],action="reject",reason="wrong_customer",expected_revision=1,current_customer=customer(connection))
    assert repo.list_for_customer(1,status="pending")==[]
    rejected,=repo.list_for_customer(1,status="rejected")
    assert rejected["evidence_count"]==2
    assert repo.decide(1,second["id"],action="reject",expected_revision=1,current_customer=customer(connection))["id"]==first["id"]
    assert connection.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0]==1


def test_new_named_contact_is_added_with_stable_id_and_field_provenance(store):
    connection,repo=store
    contact_add(repo,{"name":"New Synthetic Person","role":"Planning","email":"new@example.org","phone":"030 12345678"})
    item,=repo.list_for_customer(1,status="pending")
    accepted=repo.decide(1,item["id"],action="accept",expected_revision=1,current_customer=contact_customer(connection))
    assert accepted["status"]=="accepted"
    contacts=contact_customer(connection)["contacts"]
    assert len(contacts)==1 and contacts[0]["id"]
    assert contacts[0]["name"]=="New Synthetic Person"
    assert connection.execute("SELECT count(*) FROM customer_field_provenance WHERE target_id=? AND origin='confirmed'",(contacts[0]["id"],)).fetchone()[0]==4


def test_accepting_contact_already_present_is_idempotent(store):
    connection,repo=store
    connection.execute("INSERT INTO contacts(customer_id,uid,name,email,phone) VALUES (1,'contact-stable','Mira Muster','mira@example.org','+49 30 12345678')")
    contact_add(repo,{"name":"Mira Muster","role":"","email":"mira@example.org","phone":"030 12345678"})
    item,=repo.list_for_customer(1,status="pending")
    for _ in range(2):
        repo.decide(1,item["id"],action="accept",expected_revision=1,current_customer=contact_customer(connection))
    assert connection.execute("SELECT count(*) FROM contacts").fetchone()[0]==1
    assert customer(connection)["revision"]==1
    assert connection.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0]==1


def test_legacy_contact_decision_preserves_named_target_under_normalization(store):
    connection,repo=store
    connection.execute("DELETE FROM candidate_schema_metadata WHERE key='canonical_version'")
    connection.execute("INSERT INTO customer_document_suggestions(customer_id,kind,value,source_path,fingerprint,confidence,status,suggestion_type,contact_name,contact_email,contact_phone) VALUES (1,'email','legacy contact','source://synthetic/old-contact.txt','old-contact',0,'rejected','contact','Mira Muster','mira@example.org','030 12345678')")
    repo=SqliteCustomerSuggestionRepository(connection)
    contact_add(repo,{"name":"Mira Muster","role":"","email":"mira@example.org","phone":"+49 30 12345678"})
    assert repo.list_for_customer(1,status="pending")==[]
    rejected,=repo.list_for_customer(1,status="rejected")
    assert rejected["contact"]["name"]=="Mira Muster"
    assert rejected["field_name"]=="contact"


def test_atomic_customer_conflict_and_stale_revision_never_write_any_address_field(store):
    from papagui_server.domain.errors import CustomerConflictError
    connection,repo=store
    connection.execute("UPDATE customers SET city='Bonn' WHERE id=1")
    payload={"street":"Musterstraße 12","postal_code":"10115","city":"Berlin"}
    add(repo,kind="address",value=json.dumps(payload),payload=payload)
    item,=repo.list_for_customer(1,status="pending")
    assert item["is_conflict"]
    with pytest.raises(CustomerConflictError):
        repo.decide(1,item["id"],action="accept",expected_revision=0,current_customer=customer(connection))
    with pytest.raises(SuggestionOverwriteError):
        repo.decide(1,item["id"],action="accept",expected_revision=1,current_customer=customer(connection))
    assert customer(connection)["street"]=="" and customer(connection)["postal_code"]==""
    assert customer(connection)["city"]=="Bonn" and customer(connection)["revision"]==1


def test_pending_values_reconcile_against_manual_edit_and_return_after_clear(store):
    connection,repo=store
    add(repo)
    connection.execute("UPDATE customers SET phone='+49 30 12345678' WHERE id=1")
    repo.reconcile_customer(customer(connection))
    assert repo.list_for_customer(1,status="pending")==[]
    connection.execute("UPDATE customers SET phone='' WHERE id=1")
    repo.reconcile_customer(customer(connection))
    assert len(repo.list_for_customer(1,status="pending"))==1


def test_pages_have_stable_order_and_hide_address_groups_for_older_clients(store):
    connection,repo=store
    add(repo)
    add(repo,kind="email",value="one@example.org")
    payload={"street":"Musterstraße 12","postal_code":"10115","city":"Berlin"}
    add(repo,kind="address",value=json.dumps(payload),payload=payload)
    all_items=repo.list_for_customer(1,status="pending")
    pages=[repo.list_for_customer(1,status="pending",limit=1,offset=index)[0] for index in range(3)]
    assert [item["id"] for item in pages]==[item["id"] for item in all_items]
    assert len(repo.list_for_customer(1,status="pending",include_groups=False))==2
    with pytest.raises(ValueError):
        repo.list_for_customer(1,status="pending",limit=0)
