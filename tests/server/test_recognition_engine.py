"""Synthetic-only semantic regressions; never open a customer corpus or database."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from papagui_server.domain.customer_recognition import (
    CustomerIdentityPolicy, DocumentCandidate, document_candidates, normalize_candidate_value,
)
from papagui_server.domain.recognition_values import valid_contact_name

CUSTOMER = "Ada Beispiel"


def fields(text: str, **kwargs):
    return [item for item in document_candidates(text, customer_name=CUSTOMER, **kwargs) if item.field_name in {"phone", "email"}]


@pytest.mark.parametrize("raw,normalized", [
    ("030 12345678", "+493012345678"),
    ("+49 (0)30 12345678", "+493012345678"),
    ("0049 30 12345678", "+493012345678"),
    ("+44 20 7946 0958", "+442079460958"),
    ("+1 202-555-0123", "+12025550123"),
    ("+49 30 12345678 ext. 42", "+493012345678;ext=42"),
])
def test_phone_is_validated_internationally_and_extension_is_distinct(raw, normalized):
    values = fields(f"Auftraggeber: {CUSTOMER}\nTelefon: {raw}")
    assert [(item.normalized_value, item.quality) for item in values] == [(normalized, "strong")]
    assert normalize_candidate_value("phone", raw) == normalized


@pytest.mark.parametrize("line", [
    "Datum: 09.09.2026", "Datum: 2026-09-09", "Rechnungsnummer: 030 12345678",
    "Rechnung 030 12345678", "IBAN: DE89 3704 0044 0532 0130 00",
    "Steuernummer: 030 12345678", "Flurstück: 030 12345678", "Position: 030 12345678",
    "Menge: 030 12345678", "Preis: 030 12345678", "Fax: 030 12345678",
    "Telefax: +44 20 7946 0958", "Kundennummer: 030 12345678", "Betrag: 030 12345678",
    "030 12345678 EUR", "030 12345678 kg", "030  12345678  450,00", "Telefon: 12",
    "Telefon: 030\n12345678", "Menge\n123\n456\n789", "PLZ: 10115", "Datum: 03.01.2345",
])
def test_negative_numeric_contexts_never_create_phone(line):
    assert fields(f"Kunde: {CUSTOMER}\n{line}") == []


def test_nearest_contact_label_separates_fax_and_phone():
    values = fields(f"Kunde: {CUSTOMER}\nFax: 030 12345678; Telefon: +44 20 7946 0958")
    assert [item.normalized_value for item in values] == ["+442079460958"]


@pytest.mark.parametrize("raw", [
    "03. 01. 2025", "03 . 01 . 2025", "03 / 01 / 2025", "03 - 01 - 2025",
    "03. 01. 25", "03 01 2025", "2025.03.01", "2025 / 03 / 01", "2025 03 01",
    "03\u00a001\u00a02025", "03\u201301\u20132025", "(03. 01. 2025)",
])
def test_date_shapes_never_become_phone_even_with_ocr_spacing_or_phone_label(raw):
    assert normalize_candidate_value("phone", raw) == ""
    for label in ("", "Datum: ", "Telefon: "):
        assert fields(f"Kunde: {CUSTOMER}\n{label}{raw}") == []


@pytest.mark.parametrize("date_label", [
    "Datum", "Stand", "Erstellt", "Geändert", "Gedruckt", "Geburtstag", "Geburtsdatum",
    "Termin", "Gültig bis",
])
def test_date_labels_reject_ambiguous_compact_values_without_hiding_nearby_phone(date_label):
    assert not fields(f"Kunde: {CUSTOMER}\n{date_label}: 03012025")
    for line in (
        f"{date_label}: 03012025; Telefon: +44 20 7946 0958",
        f"Telefon: +44 20 7946 0958 | {date_label}: 03012025",
        f"{date_label}: 03. 01. 2025 Telefon: +44 20 7946 0958",
        f"Telefon: +44 20 7946 0958 {date_label}: 03. 01. 2025",
    ):
        assert [item.normalized_value for item in fields(f"Kunde: {CUSTOMER}\n{line}")] == ["+442079460958"]


@pytest.mark.parametrize("raw", ["03012025", "030 12 2025", "+49 30 12 2025"])
def test_ambiguous_phone_digits_remain_usable_with_explicit_phone_context(raw):
    values = fields(f"Kunde: {CUSTOMER}\nTelefon: {raw}")
    assert len(values) == 1 and values[0].quality == "strong"
    assert values[0].normalized_value == normalize_candidate_value("phone", raw)


@pytest.mark.parametrize("raw", ["a..b@example.org", ".broken@example.org", "name@example..org", "name@example", "name@-example.org", "name @example.org"])
def test_invalid_email_is_rejected(raw):
    assert not fields(f"Kunde: {CUSTOMER}\nE-Mail: {raw}")
    assert not normalize_candidate_value("email", raw)


def test_email_domain_normalized_plus_and_local_parts_preserved():
    values = fields(f"Kunde: {CUSTOMER}\nE-Mail: Ada+projekt@EXAMPLE.ORG\nE-Mail: Ada+buero@example.org")
    assert {item.normalized_value for item in values} == {"Ada+projekt@example.org", "Ada+buero@example.org"}
    assert all("valid-email-syntax" in item.reasons for item in values)


@pytest.mark.parametrize("role", ["Absender", "Empfänger", "Auftraggeber", "Bauherr", "Rechnung an"])
def test_customer_can_be_sender_or_recipient(role):
    values = fields(f"{role}: {CUSTOMER}\nTelefon: 030 12345678")
    assert len(values) == 1 and values[0].quality == "strong"


@pytest.mark.parametrize("role", ["Lieferant", "Nachunternehmer", "Auftragnehmer", "Behörde", "Bauamt", "Baustelle", "Projektadresse", "Bauvorhaben"])
def test_other_parties_never_become_customer_fields(role):
    assert not fields(f"{role}: Fremde Partei\nTelefon: 030 12345678\nMail: fremd@example.org")


def test_own_customer_supplier_and_unknown_remain_separate():
    content = (
        "Absender: Eigenes Planungsbüro\nTel: 030 12345678\nMail: eigen@example.org\n"
        f"Empfänger: {CUSTOMER}\nTel: +44 20 7946 0958\nMail: ada@example.org\n"
        "Lieferant: Beispiel Zulieferung\nTel: +1 202-555-0123\nMail: lieferung@example.org\n\n"
        "Absender: Unbekannter Dritter\nMail: unbekannt@example.org"
    )
    values = fields(content, own_identities=("Eigenes Planungsbüro",))
    strong = {item.normalized_value for item in values if item.quality == "strong"}
    assert strong == {"+442079460958", "ada@example.org"}
    assert [(item.normalized_value, item.party_role) for item in values if item.quality == "review"] == [("unbekannt@example.org", "unknown")]


def test_identity_is_not_a_substring_match_and_role_conflict_is_review():
    assert all(item.quality == "review" for item in fields("Auftraggeber: Ada Beispielmann\nMail: a@example.org"))
    assert all(item.quality == "review" for item in fields("Absender: Ein anderes Büro\nTelefon: 030 12345678"))


def test_explicit_customer_role_without_named_contradiction_is_usable():
    values = fields("Auftraggeber:\nTelefon: 030 12345678")
    assert values and values[0].quality == "strong"
    assert "explicit-customer-role" in values[0].reasons


def test_aliases_are_accepted_only_when_explicitly_passed():
    text = "Absender: A. Beispiel\nMail: ada@example.org"
    assert fields(text)[0].quality == "review"
    assert fields(text, customer_aliases=("A. Beispiel",))[0].quality == "strong"


def test_unresolved_values_have_separate_party_keys_without_silent_display_limit():
    values = document_candidates("\n\n".join(f"Partei {n}\nMail: contact{n}@example.org" for n in range(8)))
    assert len(values) == 8
    assert len({item.party_key for item in values}) == 8
    assert all(item.quality == "review" for item in values)


def test_full_address_is_atomic_and_project_address_excluded():
    values = document_candidates(f"Kunde: {CUSTOMER}\nMusterstraße 12\n10115 Berlin\n\nBaustelle: Zweiter Ort\nAndere Straße 7\n20095 Hamburg", customer_name=CUSTOMER)
    address, = [item for item in values if item.field_name == "address"]
    assert address.payload == {"street": "Musterstraße 12", "postal_code": "10115", "city": "Berlin"}
    assert address.suggestion_type == "address"
    assert not any(item.field_name in {"street", "postal_code", "city"} for item in values)


@pytest.mark.parametrize("text", [
    f"Kunde: {CUSTOMER}\nMusterstraße 12\n\n10115 Berlin",
    f"Kunde: {CUSTOMER}\nPostfach 12\n10115 Berlin",
    f"Kunde: {CUSTOMER}\nMusterstraße 12\n8000 Zürich",
    f"Kunde: {CUSTOMER}\nRechnung 12345\n10115 Berlin",
])
def test_incomplete_postbox_and_foreign_address_never_invent_domestic_address(text):
    assert not any(item.field_name == "address" and item.quality == "strong" for item in document_candidates(text, customer_name=CUSTOMER))


@pytest.mark.parametrize("lines,kind,postal,city", [
    ("Postfach 12\n10115 Berlin", "postbox", "10115", "Berlin"),
    ("Beispielgasse 12\n8000 Zürich", "foreign_format", "8000", "Zürich"),
    ("10 Example Road\nSW1A 1AA London", "foreign_format", "SW1A 1AA", "London"),
    ("Musterstraße 12", "incomplete", "", ""),
])
def test_nonstandard_addresses_remain_explicit_review_evidence(lines, kind, postal, city):
    values = document_candidates(f"Kunde: {CUSTOMER}\n{lines}", customer_name=CUSTOMER)
    address, = [item for item in values if item.field_name == "address"]
    assert address.quality == "review" and address.party_role == "customer"
    assert address.payload["address_kind"] == kind
    assert address.payload["postal_code"] == postal and address.payload["city"] == city
    assert "country" not in address.payload


def test_multiple_contacts_do_not_merge_shared_switchboard():
    text = (f"Kunde: {CUSTOMER}\nAnsprechpartner: Mira Muster (Planung)\nTelefon: 030 12345678\nMail: mira@example.org\n"
            "Ansprechpartner: Toni Muster\nRolle: Abrechnung\nTelefon: 030 12345678\nMail: toni@example.org")
    contacts = [item for item in document_candidates(text, customer_name=CUSTOMER) if item.suggestion_type == "contact"]
    assert len(contacts) == 2 and len({item.party_key for item in contacts}) == 2
    assert {item.payload["role"] for item in contacts} == {"Planung", "Abrechnung"}
    assert {item.payload["email"] for item in contacts} == {"mira@example.org", "toni@example.org"}
    assert all(item.payload["phone"] == "030 12345678" for item in contacts)


def test_contact_block_does_not_borrow_next_unknown_block_phone():
    values = document_candidates(f"Kunde: {CUSTOMER}\nAnsprechpartner: Mira Muster\nMail: mira@example.org\n\nTel: +44 20 7946 0958", customer_name=CUSTOMER)
    contact, = [item for item in values if item.field_name == "contact"]
    assert contact.payload["phone"] == ""
    assert next(item for item in values if item.field_name == "phone").quality == "review"


def test_invalid_named_contact_falls_back_to_review_channels_and_no_name_only_card():
    assert fields(f"Kunde: {CUSTOMER}\nKontakt: info@example.org")[0].field_name == "email"
    assert document_candidates(f"Kunde: {CUSTOMER}\nAnsprechpartner: Mira Muster", customer_name=CUSTOMER) == ()


@pytest.mark.parametrize("name", [
    None, 123, "", " ", "---", "N. N.", "nicht angegeben", "noch offen", "unknown",
    "Statische Berechnung", "Projektleitung Planung", "Unterschrift Bauherr",
    "Inhaltsverzeichnis Dokumentation", "Schal- und Bewehrungsplan", "Name Nachname",
    "Angebot zur Projektplanung", "Leistungen im Werkvertrag", "Abstimmung zur Tragwerksplanung",
    "Vertrag über Bauleistungen", "Service Contract", "Quotation Example", "Prüfung der Schlussrechnung",
    "Telefon: 030 12345678", "Mira Muster 2025", "kontakt@example.org", "Synthetik GmbH",
])
def test_contact_name_validator_rejects_missing_names_and_document_headings(name):
    assert not valid_contact_name(name)
    text = f"Kunde: {CUSTOMER}\nAnsprechpartner: {name or ''}\nTelefon: 030 12345678\nMail: kontakt@example.org"
    values = document_candidates(text, customer_name=CUSTOMER)
    assert not any(item.suggestion_type == "contact" for item in values)
    assert {item.field_name for item in values} == {"phone", "email"}


@pytest.mark.parametrize("name", [
    "Mira Muster", "A. Beispiel", "J. R. R. Muster", "Élodie D’Arcy", "Jean-Luc Beispiel", "Mira Kaplan",
    "María de la Cruz", "Ludwig van Beispiel", "Nguyễn Thị Mẫu", "Amina al-Mithal",
    "Dr. Mira Muster", "Prof. Dr.-Ing. Toni Muster", "Dipl.-Ing. Mira Muster", "Dr. Beispiel",
    "Herr Beispiel", "Frau Beispiel", "Muster, Mira", "王小明", "김민준", "علي مثال",
])
def test_contact_name_validator_accepts_international_names_without_a_given_name_dictionary(name):
    assert valid_contact_name(name)


@pytest.mark.parametrize("name", [
    "Mira Muster", "A. Beispiel", "Élodie D’Arcy", "Jean-Luc Beispiel", "María de la Cruz",
    "Dr. Mira Muster", "Herr Beispiel", "Frau Beispiel", "王小明", "김민준", "علي مثال",
])
def test_named_contact_keeps_valid_names_and_channels(name):
    values = document_candidates(
        f"Kunde: {CUSTOMER}\nAnsprechpartner: {name}\nTelefon: 030 12345678\nMail: person@example.org",
        customer_name=CUSTOMER,
    )
    contact, = values
    assert contact.suggestion_type == "contact" and contact.payload["name"] == name
    assert contact.payload["email"] == "person@example.org" and contact.quality == "strong"


def test_company_with_given_name_uses_legal_form():
    policy = CustomerIdentityPolicy(frozenset({"ada", "mira"}))
    for company in ("Ada Beispiel GmbH", "Ada Beispiel AG", "Mira Muster GmbH & Co. KG", "Mira Muster UG (haftungsbeschränkt)"):
        assert policy.entity_type(company) == "Unternehmen"
        values = document_candidates(f"Auftraggeber: {company}\nMail: firma@example.org", customer_name=company)
        assert next(item for item in values if item.field_name == "company").value == company
    assert policy.entity_type(CUSTOMER) == "Privatperson"


@pytest.mark.parametrize("role", ["Rechnung an", "Bauherrin", "Kundin", "Von"])
def test_company_role_label_and_business_digits_are_not_part_of_identity(role):
    name = "Beispiel 42 GmbH"
    values = document_candidates(f"{role}: {name}\nMail: firma@example.org", customer_name=name)
    company, = [item for item in values if item.field_name == "company"]
    assert company.value == name


def test_name_matching_conservatively_protects_similar_people_and_legal_forms():
    policy = CustomerIdentityPolicy(frozenset())
    assert policy.similar("Synthetik GmbH", "Synthetik, GmbH")
    assert not policy.similar("Synthetik GmbH", "Synthetik AG")
    assert not policy.similar("Ada Beispiel", "Eva Beispiel")
    assert not policy.similar("Ada Beispiel", "Ada Beispiele")
    assert not policy.similar("", "")


def test_same_customer_identity_does_not_depend_on_project_city():
    # Project cities are deliberately not an identity input.
    assert CustomerIdentityPolicy.similar("Synthetik GmbH", "Synthetik GmbH")


def test_structured_blocks_preserve_evidence_locators_and_repeated_evidence():
    blocks = [
        {"text": f"Kunde: {CUSTOMER}\nMail: ada@example.org", "page": 2, "bbox": [1, 2, 3, 4], "block_id": "p2b4", "extraction_method": "pdf-text"},
        SimpleNamespace(text=f"Kunde: {CUSTOMER}\nMail: ada@example.org", page=3, block_id="p3b2", kind="paragraph"),
    ]
    values = fields("Ignored fallback a@example.org", blocks=blocks)
    assert len(values) == 2
    assert values[0].source_locator == {"page": 2, "bbox": [1, 2, 3, 4], "block_id": "p2b4", "extraction_method": "pdf-text", "segment": 0}
    assert values[1].source_locator["page"] == 3


def test_docx_party_paragraphs_join_only_within_section():
    blocks = [
        {"text": f"Auftraggeber: {CUSTOMER}", "section": "document", "order": 0},
        {"text": "Musterstraße 12", "section": "document", "order": 1},
        {"text": "10115 Berlin", "section": "document", "order": 2},
        {"text": "Telefon: 030 12345678", "section": "document", "order": 3},
        {"text": "Mail: fremd@example.org", "section": "footer1", "order": 4},
    ]
    values = document_candidates("", customer_name=CUSTOMER, blocks=blocks)
    assert next(item for item in values if item.field_name == "address").quality == "strong"
    assert next(item for item in values if item.field_name == "phone").quality == "strong"
    assert next(item for item in values if item.field_name == "email").quality == "review"


def test_spreadsheet_cells_join_same_row_and_use_explicit_column_headers():
    blocks = [{"text": text, "kind": "table_cell", "sheet": "Kontakte", "cell": cell} for cell, text in [
        ("A1", "Kunde"), ("B1", "Telefon"), ("C1", "E-Mail"),
        ("A2", CUSTOMER), ("B2", "030 12345678"), ("C2", "ada@example.org"),
        ("A3", "Andere Person"), ("B3", "+44 20 7946 0958"), ("C3", "fremd@example.org"),
    ]]
    values = fields("", blocks=blocks)
    assert {item.normalized_value for item in values if item.quality == "strong"} == {"+493012345678", "ada@example.org"}
    assert all(item.source_locator["cell"] == "A2:C2" for item in values if item.quality == "strong")
    assert all(item.quality == "review" for item in values if "fremd" in item.value or "+44" in item.value)


def test_numeric_table_without_contact_column_never_becomes_phone():
    assert not fields("", blocks=[{"text": f"{CUSTOMER}\n030 12345678", "kind": "table_row"}])


def test_legacy_constructor_and_canonical_json_keys():
    value = DocumentCandidate("phone", "030 12345678", 0.75, "old-rule", "excerpt")
    assert value.quality == "review" and value.normalized_value == ""
    a = '{"phone":"030 12345678","name":"Mira Muster"}'
    b = '{"name":"mira muster", "phone":"+49 30 12345678"}'
    assert normalize_candidate_value("contact", a) == normalize_candidate_value("contact", b)
    assert normalize_candidate_value("email", "") == ""
    assert normalize_candidate_value("phone", "invalid") == ""
    assert normalize_candidate_value("address", "  Partial address ") == "partial address"
    assert normalize_candidate_value("company", "  COMPANY  NAME ") == "company name"


@pytest.mark.parametrize("method", ["ocr", "tesseract_tsv"])
@pytest.mark.parametrize("confidence,bbox,page", [(0.3,[0,0,20,20],1),(None,[0,0,20,20],1),(0.95,None,1),(0.95,[0,0,20,20],None)])
def test_ocr_needs_source_quality_and_layout_before_becoming_strong(method,confidence,bbox,page):
    block = {"text": f"Kunde: {CUSTOMER}\nTelefon: 030 12345678", "method": method, "confidence":confidence, "bbox":bbox, "page":page}
    candidate, = fields("", blocks=[block])
    assert candidate.quality == "review" and candidate.party_role == "customer"
    assert {"ocr-quality-unverified", "ocr-layout-unverified"} & set(candidate.reasons)


def test_high_quality_ocr_and_digital_text_do_not_get_missing_confidence_penalty():
    for metadata in ({"method":"ocr","confidence":0.96,"bbox":[0,0,20,20],"page":1}, {"method":"pdf-text"}):
        candidate, = fields("", blocks=[{"text": f"Kunde: {CUSTOMER}\nTelefon: 030 12345678", **metadata}])
        assert candidate.quality == "strong"


def test_authority_can_be_confirmed_customer_and_project_label_splits_address():
    values = document_candidates("Auftraggeber: Bauamt Beispiel\nTelefon: 030 12345678\nProjekt: Neubau\nMusterstraße 12\n10115 Berlin", customer_name="Bauamt Beispiel")
    assert any(item.field_name == "phone" and item.quality == "strong" for item in values)
    assert not any(item.field_name == "address" for item in values)
