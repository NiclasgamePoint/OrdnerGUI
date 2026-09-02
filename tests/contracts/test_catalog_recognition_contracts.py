from __future__ import annotations

import pytest

from papagui_contracts import (
    CatalogFacets,
    CatalogFile,
    CatalogFolder,
    CatalogProjectRoot,
    ContractValidationError,
    Customer,
    CustomerProject,
    CustomerSuggestion,
    RecognitionCase,
    RecognitionDecision,
    RecognitionEvidence,
    RecognitionRunSummary,
    SourcePath,
)


SOURCE = {"source_id": "nas", "relative_path": "Planung/2026/Kunde"}


def test_catalog_contracts_round_trip_portable_metadata() -> None:
    file_payload = {
        "id": 1,
        "source": SOURCE,
        "filename": "Angebot.pdf",
        "file_type": "pdf",
        "file_size": 123,
        "modified_date": "2026-01-01T12:00:00",
        "domain_folder": "Planung",
        "time_bucket": "2026",
        "project_name": "Kunde",
        "relative_dir": "Planung/2026/Kunde",
        "folder_id": 2,
        "project_root_id": 3,
    }
    file_dto = CatalogFile.from_dict(file_payload)
    assert CatalogFile.from_json(file_dto.to_json()) == file_dto
    assert file_dto.modified_at == "2026-01-01T12:00:00"

    folder = CatalogFolder.from_dict(
        {
            "id": 2,
            "source_id": "nas",
            "relative_path": "Planung/2026/Kunde",
            "name": "Kunde",
            "parent_id": 1,
            "project_root_id": 3,
            "file_count": 4,
            "total_size": 999,
            "last_modified": "2026-01-02",
        }
    )
    assert CatalogFolder.from_json(folder.to_json()) == folder

    project = CatalogProjectRoot.from_dict(
        {
            "id": 3,
            "source": SOURCE,
            "service_type": "Planung",
            "year": 2026,
            "customer_label": "Kunde, Köln",
            "customer_name": "Kunde",
            "city": "Köln",
            "recognition_key": "kunde",
        }
    )
    assert CatalogProjectRoot.from_json(project.to_json()) == project
    facets = CatalogFacets.from_dict(
        {"domains": ["Planung"], "years": ["2026"], "file_types": ["pdf"]}
    )
    assert CatalogFacets.from_json(facets.to_json()) == facets


def test_customer_project_is_additive_and_supports_flat_source() -> None:
    project = CustomerProject.from_dict(
        {
            "id": 4,
            "customer_id": 5,
            "project_root_id": 3,
            **SOURCE,
            "service_type": "Planung",
            "project_label": "Kunde, Köln",
            "city": "Köln",
            "year": 2026,
            "source_type": "recognition",
        }
    )
    customer = Customer.from_dict(
        {"id": 5, "revision": 1, "display_name": "Kunde", "projects": [project]}
    )
    assert customer.projects == (project,)
    assert Customer.from_json(customer.to_json()) == customer


def test_recognition_contracts_round_trip_all_review_states() -> None:
    evidence = RecognitionEvidence.from_dict(
        {
            "field_name": "email",
            "value": "kunde@example.org",
            "source": SOURCE,
            "excerpt": "Kontakt kunde@example.org",
            "rule": "email",
            "confidence": 0.9,
        }
    )
    case = RecognitionCase.from_dict(
        {
            "signature": "case-1",
            "recognition_key": "kunde",
            "display_name": "Kunde",
            "project_roots": [SOURCE],
            "cities": ["Köln"],
            "service_types": ["Planung"],
            "years": [2026],
            "reason": "similar_name",
            "suggested_customer_ids": [5],
            "evidence": [evidence],
            "status": "resolved",
        }
    )
    assert RecognitionCase.from_json(case.to_json()) == case
    decision = RecognitionDecision.from_dict(
        {
            "signature": "case-1",
            "action": "assign",
            "customer_id": 5,
            "decided_at": "2026-01-02T12:00:00Z",
        }
    )
    assert RecognitionDecision.from_json(decision.to_json()) == decision
    run = RecognitionRunSummary.from_dict(
        {
            "id": 1,
            "detected": 3,
            "created": 1,
            "assigned": 1,
            "pending": 1,
            "rejected": 0,
            "error": "",
            "started_at": "start",
            "finished_at": "finish",
        }
    )
    assert RecognitionRunSummary.from_json(run.to_json()) == run
    suggestion = CustomerSuggestion.from_dict(
        {
            "id": 7,
            "customer_id": 5,
            "kind": "email",
            "value": "kunde@example.org",
            "source": SOURCE,
            "fingerprint": "fingerprint",
            "confidence": 1,
            "status": "accepted",
        }
    )
    assert CustomerSuggestion.from_json(suggestion.to_json()) == suggestion


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (CatalogFile.from_dict, {"id": 0, "source": SOURCE, "filename": "x"}),
        (CatalogFolder.from_dict, {"id": 1, "source": SOURCE, "name": ""}),
        (
            CatalogProjectRoot.from_dict,
            {
                "id": 1,
                "source": SOURCE,
                "service_type": "x",
                "year": 1899,
                "customer_label": "x",
                "customer_name": "x",
            },
        ),
        (CatalogFacets.from_dict, {"domains": "invalid"}),
        (
            RecognitionEvidence.from_dict,
            {"field_name": "email", "value": "x", "source": SOURCE, "confidence": True},
        ),
        (
            RecognitionCase.from_dict,
            {
                "signature": "x",
                "recognition_key": "x",
                "display_name": "x",
                "project_roots": [SOURCE],
                "status": "unknown",
            },
        ),
        (
            RecognitionDecision.from_dict,
            {"signature": "x", "action": "assign"},
        ),
        (
            RecognitionRunSummary.from_dict,
            {"detected": -1},
        ),
        (
            CustomerSuggestion.from_dict,
            {
                "id": 1,
                "customer_id": 1,
                "field_name": "email",
                "value": "x",
                "source": SOURCE,
                "fingerprint": "x",
                "status": "unknown",
            },
        ),
    ],
)
def test_invalid_catalog_and_recognition_payloads_raise_contract_error(
    factory, payload
) -> None:
    with pytest.raises(ContractValidationError):
        factory(payload)


def test_direct_dto_types_remain_strict() -> None:
    source = SourcePath("nas", "a")
    with pytest.raises(ContractValidationError):
        CatalogFile(1, "not-source", "x")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError):
        CatalogFolder(1, source, "x", last_modified="")
    with pytest.raises(ContractValidationError):
        CatalogProjectRoot(1, source, "", 2026, "x", "x")
    with pytest.raises(ContractValidationError):
        RecognitionEvidence("email", "x", "bad")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError):
        RecognitionCase("x", "x", "x", ("bad",))  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError):
        CustomerSuggestion(1, 1, "email", "x", source, "x", confidence=2)
