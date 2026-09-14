from __future__ import annotations

from pathlib import Path

from papagui_server.adapters.catalog import SqliteCatalogIndexer
from papagui_server.adapters.recognition import _load_given_names
from papagui_server.adapters.sqlite_customers import SqliteCustomerUnitOfWorkFactory
from papagui_server.composition import RuntimeConfiguration, build_container
from papagui_server.domain.customer_recognition import document_candidates
from papagui_server.domain.folder_structure import normalize_identity
from papagui_server.domain.models import ServerSettings


def _catalog(data: Path, source: Path) -> Path:
    return SqliteCatalogIndexer(data).build(
        source,
        source_id="primary",
        full_rebuild=True,
        settings=ServerSettings(),
        cancelled=lambda: False,
        progress=lambda *_args: None,
    )


def test_folder_and_document_recognition_are_safe_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    project = source / "Planung" / "2026" / "Max Mustermann, Köln"
    project.mkdir(parents=True)
    (project / "Angebot.txt").write_text(
        "Kontakt: max@example.org, Telefon +49 (221) 12345678", encoding="utf-8"
    )
    old = source / "Planung" / "2010" / "Alt GmbH, Bonn"
    old.mkdir(parents=True)
    (old / "old.txt").write_text("alt", encoding="utf-8")
    invalid_year = source / "Planung" / "aktuell" / "Ignored GmbH"
    invalid_year.mkdir(parents=True)
    (invalid_year / "ignored.txt").write_text("ignored", encoding="utf-8")
    second = source / "Beratung" / "2026" / "Max Mustermann"
    second.mkdir(parents=True)
    (second / "note.txt").write_text("max@example.org", encoding="utf-8")
    data = tmp_path / "data"
    _catalog(data, source)
    container = build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=data,
            config_path=tmp_path / "config",
            allow_insecure_no_client_token=True,
        )
    )
    recognizer = container.recognition

    progress = []
    first = recognizer.synchronize(
        source, source_id="primary", minimum_year=2016,
        progress=lambda *values: progress.append(values),
    )
    assert progress[0] == ("customer-recognition", 0, 1, 0)
    assert ("customer-recognition", 1, 1, 0) in progress
    assert ("customer-documents", 0, 1, 1) in progress
    assert progress[-1] == ("customer-documents", 1, 1, 2)
    assert first == {
        "detected": 2,
        "created": 1,
        "assigned": 0,
        "pending": 0,
        "rejected": 0,
        "suggestions": 3,
    }
    factory = SqliteCustomerUnitOfWorkFactory(data / "customers.db")
    with factory() as work:
        customer = work.customers.list()[0]
        assert customer["entity_type"] == "Privatperson"
        assert set(customer["service_types"]) == {"Beratung", "Planung"}
        rows = work.suggestions.list_for_customer(customer["id"])
        work.commit()
    assert {row["field_name"] for row in rows} == {"email", "phone"}
    assert all(row["status"] == "pending" for row in rows)
    assert len(customer["projects"]) == 2

    second_result = recognizer.synchronize(
        source, source_id="primary", minimum_year=2016
    )
    assert second_result["created"] == 0
    assert second_result["assigned"] == 0
    assert second_result["suggestions"] == 0


def test_recognition_exposes_conflicting_values_without_changing_master_data(tmp_path: Path) -> None:
    source = tmp_path / "source"
    project = source / "Service" / "2026" / "Firma GmbH, Berlin"
    project.mkdir(parents=True)
    (project / "contact.txt").write_text(
        "mail office@example.org phone 030 12345678", encoding="utf-8"
    )
    data = tmp_path / "data"
    _catalog(data, source)
    factory = SqliteCustomerUnitOfWorkFactory(data / "customers.db")
    factory.initialize()
    with factory() as work:
        work.customers.create(
            {
                "display_name": "Firma GmbH",
                "email": "known@example.org",
                "phone": "030 999999",
            }
        )
        work.commit()
    container = build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=data,
            config_path=tmp_path / "config",
            allow_insecure_no_client_token=True,
        )
    )
    result = container.recognition.synchronize(
        source, source_id="primary", minimum_year=2016
    )
    assert result["suggestions"] == 2
    with factory() as work:
        current = work.customers.list()[0]
        assert current["email"] == "known@example.org"
        assert current["phone"] == "030 999999"
        suggestions = work.suggestions.list_for_customer(current["id"], status="pending")
        assert {value["field_name"] for value in suggestions} == {"email", "phone"}
        assert all(value["is_conflict"] for value in suggestions)


def test_recognition_helpers_handle_invalid_inputs(tmp_path: Path, monkeypatch) -> None:
    assert normalize_identity("  Änne & SÖHNE!!! ") == "änne söhne"
    values = document_candidates("Mail a@example.org, Telefon 030 12345678")
    assert {item.field_name for item in values} == {"email", "phone"}

    class BrokenResource:
        def joinpath(self, _name):
            return self

        def read_text(self, **_kwargs):
            raise OSError("unavailable")

    monkeypatch.setattr(
        "papagui_server.adapters.recognition.files", lambda _package: BrokenResource()
    )
    assert _load_given_names() == frozenset()
