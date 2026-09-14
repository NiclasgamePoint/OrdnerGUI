"""End-to-end document recognition using only generated synthetic customer data."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
import openpyxl

from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.adapters.catalog_reader import SqliteCatalogReader
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.application.document_recognition import DocumentRecognitionService
from papagui_server.domain.errors import ResourceNotFoundError
from papagui_server.domain.models import ServerSettings


NAME = "Beispiel GmbH"
CONTACT = "Auftraggeber: Beispiel GmbH\nTelefon: +49 30 12345678\nE-Mail: kontakt@example.org"


def test_measurement_rows_are_excluded_from_persisted_phone_and_contact_suggestions(tmp_path):
    measurement = "0.81 10.0 9.3 62.2 25.8"
    fixture = setup(tmp_path, {
        "Berechnung.txt": f"Kunde: {NAME}\n" + (measurement + "\n") * 8,
        "Kontakt.txt": (
            f"Kunde: {NAME}\nTelefon: 040/12345678\n"
            f"Ansprechpartner: Mira Muster\nTelefon: {measurement}\nE-Mail: mira@example.org"
        ),
    })
    fixture.service.run("primary", customer_id=fixture.customer_id, exhaustive=True)
    status, suggestions, current = state(fixture)
    phones = [row for row in suggestions if row["field_name"] == "phone"]
    assert [row["normalized_value"] for row in phones] == ["+494012345678"]
    contacts = [row for row in suggestions if row["field_name"] == "contact"]
    assert len(contacts) == 1
    assert contacts[0]["payload"]["email"] == "mira@example.org"
    assert contacts[0]["payload"]["phone"] == ""
    assert current["phone"] == ""
    assert status["pipeline_version"] == "customer-recognition-5"


def setup(tmp_path, documents, *, settings=None, assign=True, create_root=True):
    settings = settings or ServerSettings()
    source = tmp_path / "synthetic-source"
    source.mkdir(parents=True)
    project = source / "Planung" / "2026" / NAME
    if create_root:
        project.mkdir(parents=True)
    for name, text in documents.items():
        (project / name).write_text(text, encoding="utf-8")
    data = tmp_path / "synthetic-server"
    indexer = SqliteCatalogIndexer(data)

    def rebuild():
        return indexer.build(
            source,
            source_id="primary",
            full_rebuild=True,
            settings=settings,
            cancelled=lambda: False,
            progress=lambda *_: None,
        )

    rebuild()
    reader = SqliteCatalogReader(indexer.active_path)
    factory = SqliteCustomerUnitOfWorkFactory(data / "customers.db")
    factory.initialize()
    with factory() as work:
        customer = work.customers.create({"display_name": NAME, "entity_type": "Unternehmen"})
        if assign:
            for root in reader.list_project_roots(source_id="primary"):
                work.projects.upsert(root, customer_id=customer["id"])
        work.commit()
    service = DocumentRecognitionService(factory, reader, SimpleNamespace(load=lambda: settings))
    return SimpleNamespace(
        service=service,
        factory=factory,
        reader=reader,
        indexer=indexer,
        project=project,
        customer_id=customer["id"],
        rebuild=rebuild,
        data=data,
        settings=settings,
    )


def state(fixture):
    with fixture.factory() as work:
        status = work.suggestions.recognition_status(fixture.customer_id)
        suggestions = work.suggestions.list_for_customer(fixture.customer_id, status="pending")
        customer = work.customers.get(fixture.customer_id)
        work.commit()
    return status, suggestions, customer


@pytest.mark.parametrize("legacy_text", [False, True])
def test_excel_phone_evidence_retires_on_reassessment_without_losing_other_sources(
    tmp_path, legacy_text,
):
    fixture = setup(tmp_path, {"Kontakt.txt": CONTACT})
    spreadsheet = fixture.project / "Kontakte.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = (
        "Auftraggeber: Beispiel GmbH\nTelefon: 030 12345678\n"
        "Mobil: +44 20 7946 0958\nMail: excel@example.org\n"
        "Ansprechpartner: Mira Muster\nTelefon: 030 12345678\nMail: mira@example.org"
    )
    workbook.save(spreadsheet)
    workbook.close()
    before = hashlib.sha256(spreadsheet.read_bytes()).hexdigest()
    fixture.rebuild()
    source = "source://primary/Planung/2026/Beispiel GmbH/Kontakte.xlsx"
    contact = {"name": "Mira Muster", "role": "", "email": "mira@example.org", "phone": "030 12345678"}
    with fixture.factory() as work:
        # Reproduce candidates saved by the previous recognition policy.
        for kind, value in (("phone", "030 12345678"), ("phone", "+44 20 7946 0958"),
                            ("phone", "+1 202-555-0123"), ("contact", json.dumps(contact))):
            work.suggestions.add(
                fixture.customer_id, kind=kind, value=value, source_path=source,
                excerpt="Synthetic old Excel evidence", fingerprint="", confidence=0,
                quality="strong", party_role="customer", run_id="old",
                engine_version="customer-recognition-3", source_locator={"sheet": "Sheet", "cell": "A1"},
                suggestion_type="contact" if kind == "contact" else "field",
                payload=contact if kind == "contact" else None,
            )
        pending = work.suggestions.list_for_customer(fixture.customer_id, status="pending")
        accepted = next(row for row in pending if row["value"] == "+1 202-555-0123")
        current = work.customers.get(fixture.customer_id)
        work.suggestions.decide(
            fixture.customer_id, accepted["id"], action="accept",
            expected_revision=current["revision"], current_customer=current,
        )
        work.commit()
    if legacy_text:
        fixture.service._catalog = SimpleNamespace(
            list_project_roots=fixture.reader.list_project_roots,
            document_evidence=lambda **kwargs: [
                {**document, "blocks": []}
                for document in fixture.reader.document_evidence(**kwargs)
            ],
        )
    fixture.service.run("primary", customer_id=fixture.customer_id, exhaustive=True)
    status, suggestions, customer = state(fixture)
    phones = [row for row in suggestions if row["field_name"] == "phone"]
    assert len(phones) == 1
    assert phones[0]["normalized_value"] == "+493012345678"
    assert phones[0]["source"]["relative_path"].endswith("Kontakt.txt")
    assert phones[0]["evidence_count"] == 1
    assert customer["phone"] == "+1 202-555-0123"  # A confirmed value is retained.
    assert any(row["value"] == "excel@example.org" for row in suggestions)
    contacts = [row for row in suggestions if row["field_name"] == "contact"]
    assert contacts and all(not row["payload"].get("phone") for row in contacts)
    assert status["pipeline_version"] == "customer-recognition-5"
    assert hashlib.sha256(spreadsheet.read_bytes()).hexdigest() == before


def test_adaptive_second_round_finds_document_beyond_initial_24(tmp_path: Path) -> None:
    documents = {
        f"Angebot-Auftrag-Vertrag-{number:02}.txt": f"Synthetische Berechnung ohne Kontakte {number}"
        for number in range(24)
    }
    documents["Z99.txt"] = CONTACT
    fixture = setup(
        tmp_path,
        documents,
        settings=ServerSettings(
            priority_documents_per_project=24, recognition_documents_per_project_max=48
        ),
    )
    first = fixture.reader.document_evidence(source_id="primary", documents_per_project=24)
    assert not any(document["source"]["relative_path"].endswith("Z99.txt") for document in first)
    outcome = fixture.service.run("primary", customer_id=fixture.customer_id)
    status, suggestions, customer = state(fixture)
    assert outcome["evaluated"] == 25
    assert any(
        row["field_name"] == "email" and row["value"] == "kontakt@example.org"
        for row in suggestions
    )
    assert status["state"] == "complete"
    assert status["counts"]["evaluated"] == 25
    assert not customer["email"]  # Recognition proposes; accepting remains explicit.
    assert fixture.service.run("primary", customer_id=fixture.customer_id)["suggestions"] == 0


@pytest.mark.parametrize(
    "documents,assign,create_root,expected_state,expected_reason",
    [
        ({"empty.txt": ""}, True, True, "partial", "no_text"),
        ({"unsupported.bin": "synthetic"}, True, True, "partial", "unsupported"),
        ({}, True, True, "complete", "no_documents"),
        ({}, False, False, "complete", "no_projects"),
        (
            {"plain.txt": "Synthetic text without contact information."},
            True,
            True,
            "complete",
            "no_candidates",
        ),
    ],
)
def test_coverage_explains_empty_results(
    tmp_path, documents, assign, create_root, expected_state, expected_reason
):
    fixture = setup(tmp_path, documents, assign=assign, create_root=create_root)
    fixture.service.run("primary", customer_id=fixture.customer_id)
    status, suggestions, _ = state(fixture)
    assert (status["state"], status["reason"]) == (expected_state, expected_reason)
    assert not suggestions
    if expected_reason == "no_text":
        assert status["counts"]["extraction_problems"] == 1


def test_disabled_and_budget_exhaustion_are_distinct(tmp_path: Path) -> None:
    disabled = setup(
        tmp_path / "disabled",
        {"Kontakt.txt": CONTACT},
        settings=ServerSettings(recognition_pipeline_enabled=False),
    )
    disabled.service.run("primary", customer_id=disabled.customer_id)
    assert state(disabled)[0]["state"] == "disabled"
    bounded = setup(
        tmp_path / "bounded",
        {"a.txt": "synthetic A", "b.txt": CONTACT},
        settings=ServerSettings(
            priority_documents_per_project=1, recognition_documents_per_project_max=1
        ),
    )
    bounded.service.run("primary", customer_id=bounded.customer_id)
    status = state(bounded)[0]
    assert status["state"] == "partial"
    assert (status["counts"]["evaluated"], status["counts"]["extracted"]) == (1, 2)


@pytest.mark.parametrize("mutation", ["revision", "ownership", "delete"])
def test_changes_during_parsing_prevent_stale_customer_writes(tmp_path, monkeypatch, mutation):
    import papagui_server.application.document_recognition as module

    fixture = setup(tmp_path, {"Kontakt.txt": CONTACT})
    real_candidates = module.document_candidates
    with fixture.factory() as work:
        other = work.customers.create({"display_name": "Anderer synthetischer Kunde"})
        work.commit()

    def changing_candidates(*args, **kwargs):
        values = real_candidates(*args, **kwargs)
        # A separate writer succeeds here: expensive recognition holds no writer lock.
        if mutation == "ownership":
            with sqlite3.connect(fixture.data / "customers.db") as connection:
                connection.execute(
                    "UPDATE customer_projects SET customer_id=? WHERE customer_id=?",
                    (other["id"], fixture.customer_id),
                )
        else:
            with fixture.factory() as work:
                current = work.customers.get(fixture.customer_id)
                if mutation == "revision":
                    work.customers.update(
                        fixture.customer_id, {"phone": "030 98765432"}, current["revision"]
                    )
                else:
                    work.customers.delete(fixture.customer_id, current["revision"])
                work.commit()
        return values

    monkeypatch.setattr(module, "document_candidates", changing_candidates)
    result = fixture.service.run("primary", customer_id=fixture.customer_id)
    assert result["suggestions"] == 0
    if mutation == "delete":
        assert state(fixture)[2] is None
    else:
        status, suggestions, customer = state(fixture)
        assert status["state"] == "partial"
        assert status["counts"]["changed_during_run"] == 1
        assert not suggestions
        if mutation == "revision":
            assert customer["phone"] == "030 98765432"


def test_run_uses_immutable_catalog_snapshot_during_rebuild(tmp_path: Path, monkeypatch) -> None:
    import papagui_server.application.document_recognition as module

    fixture = setup(tmp_path, {"Kontakt.txt": CONTACT})
    original_version = fixture.reader.snapshot_version()
    real_candidates = module.document_candidates
    rebuilt = False

    def rebuild_during_candidates(*args, **kwargs):
        nonlocal rebuilt
        if not rebuilt:
            rebuilt = True
            (fixture.project / "Kontakt.txt").write_text(
                CONTACT.replace("kontakt@example.org", "new@example.org")
            )
            (fixture.project / "later.txt").write_text("synthetic later file")
            fixture.rebuild()
        return real_candidates(*args, **kwargs)

    monkeypatch.setattr(module, "document_candidates", rebuild_during_candidates)
    result = fixture.service.run("primary", customer_id=fixture.customer_id)
    assert result["evaluated"] == 1
    _, suggestions, _ = state(fixture)
    assert any(row["value"] == "kontakt@example.org" for row in suggestions)
    assert not any(row["value"] == "new@example.org" for row in suggestions)
    with sqlite3.connect(fixture.data / "customers.db") as connection:
        version = connection.execute(
            "SELECT catalog_version FROM customer_recognition_status WHERE customer_id=?",
            (fixture.customer_id,),
        ).fetchone()[0]
    assert version == original_version
    assert fixture.reader.snapshot_version() != original_version


def test_missing_customer_and_cancel_are_reported_without_false_success(tmp_path: Path) -> None:
    fixture = setup(tmp_path, {"Kontakt.txt": CONTACT})
    with pytest.raises(ResourceNotFoundError):
        fixture.service.run("primary", customer_id=99999)
    with pytest.raises(InterruptedError):
        fixture.service.run("primary", customer_id=fixture.customer_id, cancelled=lambda: True)
    assert state(fixture)[0]["state"] == "not_evaluated"


def test_deleted_and_definitively_empty_documents_retire_evidence(tmp_path: Path) -> None:
    fixture = setup(tmp_path, {"Kontakt.txt": CONTACT})
    fixture.service.run("primary", customer_id=fixture.customer_id)
    assert state(fixture)[1]
    (fixture.project / "Kontakt.txt").write_text("")
    fixture.rebuild()
    fixture.service.run("primary", customer_id=fixture.customer_id)
    assert not state(fixture)[1]
    (fixture.project / "Kontakt.txt").write_text(CONTACT)
    fixture.rebuild()
    fixture.service.run("primary", customer_id=fixture.customer_id)
    assert state(fixture)[1]
    (fixture.project / "Kontakt.txt").unlink()
    fixture.rebuild()
    fixture.service.run("primary", customer_id=fixture.customer_id)
    assert not state(fixture)[1]


def test_temporary_extraction_failure_keeps_prior_evidence_until_repair(tmp_path: Path) -> None:
    from papagui_server.domain.document_extraction import ExtractionResult
    from papagui_server.adapters.catalog_extraction import DocumentTextExtractor

    fixture = setup(tmp_path, {"Kontakt.txt": CONTACT})
    fixture.service.run("primary", customer_id=fixture.customer_id)
    previous_ids = {row["id"] for row in state(fixture)[1]}
    assert previous_ids

    class MissingTool:
        def fingerprint(self, settings):
            return "synthetic-missing-tool"

        def extract_document(self, path, settings, cancelled):
            return ExtractionResult(status="tool_missing", reason="synthetic_tool_missing")

    fixture.indexer.extractor = MissingTool()
    fixture.rebuild()
    fixture.service.run("primary", customer_id=fixture.customer_id)
    status, suggestions, _ = state(fixture)
    assert status["state"] == "partial"
    assert status["counts"]["extraction_problems"] == 1
    assert {row["id"] for row in suggestions} == previous_ids
    fixture.indexer.extractor = DocumentTextExtractor()
    fixture.rebuild()
    fixture.service.run("primary", customer_id=fixture.customer_id)
    assert state(fixture)[0]["state"] == "complete"
    assert {row["id"] for row in state(fixture)[1]} == previous_ids


def test_limited_recheck_preserves_existing_unexamined_document_evidence(tmp_path: Path) -> None:
    fixture = setup(
        tmp_path,
        {
            "Kontakt-A.txt": CONTACT,
            "Kontakt-B.txt": CONTACT.replace("kontakt@example.org", "second@example.org"),
        },
    )
    fixture.service.run("primary", customer_id=fixture.customer_id)
    previous_ids = {row["id"] for row in state(fixture)[1]}
    settings = ServerSettings(
        priority_documents_per_project=1, recognition_documents_per_project_max=1
    )
    fixture.service._settings = SimpleNamespace(load=lambda: settings)
    fixture.service.run("primary", customer_id=fixture.customer_id)
    status, suggestions, _ = state(fixture)
    assert status["state"] == "partial"
    assert {row["id"] for row in suggestions} == previous_ids


def test_legacy_reader_and_all_customer_paging_remain_supported(tmp_path: Path) -> None:
    from papagui_server.application.document_recognition import all_customers

    fixture = setup(tmp_path, {"Kontakt.txt": CONTACT})

    class LegacyReader:
        def list_project_roots(self, **kwargs):
            return fixture.reader.list_project_roots(**kwargs)

        def document_evidence(self, **kwargs):
            return fixture.reader.document_evidence(**kwargs)

    legacy = DocumentRecognitionService(fixture.factory, LegacyReader())
    assert legacy.run("primary")["evaluated"] == 1
    assert state(fixture)[1]
    offsets = []

    class PagedCustomers:
        def list(self, *, limit, offset):
            offsets.append(offset)
            return list(range(offset, min(offset + limit, 501)))

    assert len(all_customers(PagedCustomers())) == 501
    assert offsets == [0, 500]


def test_early_completion_skips_deeper_rounds_when_all_needed_fields_found(tmp_path: Path) -> None:
    document = "Auftraggeber: Beispiel GmbH\nAnsprechpartner: Mira Muster\nTelefon: 030 12345678\nE-Mail: mira@example.org"
    fixture = setup(
        tmp_path,
        {"Kontakt.txt": document, "plain.txt": "synthetic unrelated"},
        settings=ServerSettings(
            priority_documents_per_project=1, recognition_documents_per_project_max=4
        ),
    )
    with fixture.factory() as work:
        current = work.customers.get(fixture.customer_id)
        work.customers.update(
            fixture.customer_id,
            {
                "company": NAME,
                "phone": "030 55555555",
                "email": "office@example.org",
                "street": "Synthetische Straße 12",
                "postal_code": "12345",
                "city": "Teststadt",
            },
            current["revision"],
        )
        work.commit()
    assert fixture.service.run("primary", customer_id=fixture.customer_id)["evaluated"] == 1
