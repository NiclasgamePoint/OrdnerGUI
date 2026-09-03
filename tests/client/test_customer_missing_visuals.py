from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from papagui_client.gui.customer_detail import CustomerDataSuggestionsDialog
from papagui_client.gui.customer_editor import CustomerEditorDialog
from papagui_contracts import Contact, Customer, CustomerProject, SourcePath
from papagui_contracts.recognition import CustomerSuggestion


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _label_texts(widget) -> set[str]:
    return {label.text() for label in widget.findChildren(QLabel)}


def test_editor_files_page_keeps_both_old_lists_and_server_projects_read_only(
    application,
):
    projects = (
        CustomerProject(
            id=1,
            customer_id=7,
            source=SourcePath("archive", "2025/Planung/Muster"),
            project_label="Muster Zentrale",
            service_type="Planung",
            year=2025,
        ),
        CustomerProject(
            id=2,
            customer_id=7,
            source=SourcePath("archive", "2026/Beratung/Muster"),
            service_type="Beratung",
            year=2026,
        ),
    )
    customer = Customer(id=7, display_name="Muster GmbH", projects=projects)
    dialog = CustomerEditorDialog(customer)

    labels = _label_texts(dialog.tabs.widget(3))
    assert "Gefundene mögliche Ordner" in labels
    assert "Ausgewählte Ordner" in labels
    assert dialog.selected_folders_table.objectName() == "SelectedCustomerFoldersTable"
    assert dialog.found_folders_table.objectName() == "SuggestedCustomerFoldersTable"
    assert dialog.selected_folders_table.rowCount() == 2
    assert dialog.selected_folders_table.item(0, 0).text() == "Muster Zentrale"
    assert dialog.selected_folders_table.item(1, 0).text() == (
        "archive:2026/Beratung/Muster"
    )
    assert dialog.customer().projects == projects

    for button in (dialog.remove_folder_button, dialog.select_folder_button):
        assert not button.isEnabled()
        assert "Server-API-Vertrag" in button.toolTip()
    assert not dialog.found_folders_table.isEnabled()
    assert "Server-API" in dialog.project_assignment_status.text()
    dialog.close()


def test_contact_suggestion_restores_details_and_manual_conflict_warning(application):
    customer = Customer(
        id=7,
        display_name="Muster GmbH",
        contacts=(
            Contact(
                name="Ada Lovelace",
                role="Planung",
                email="ada@example.test",
                phone="123",
            ),
        ),
    )
    proposed = Contact(
        name="Ada Lovelace",
        role="Einkauf",
        email="ada@example.test",
        phone="456",
    )
    suggestion = CustomerSuggestion(
        id=4,
        customer_id=7,
        field_name="contact_name",
        value=proposed.name,
        source=SourcePath("archive", "2026/Muster/Brief.pdf"),
        fingerprint="contact-fingerprint",
        suggestion_type="contact",
        contact=proposed,
    )

    dialog = CustomerDataSuggestionsDialog(
        (suggestion,), 3, customer=customer
    )
    texts = _label_texts(dialog)
    assert "Ansprechpartner: Ada Lovelace" in texts
    assert "Rolle: Einkauf" in texts
    assert "E-Mail: ada@example.test" in texts
    assert "Telefon: 456" in texts
    warnings = dialog.findChildren(QLabel, "PopupWarning")
    assert len(warnings) == 1
    assert "manuell gepflegten Kontaktdaten" in warnings[0].text()
    assert "Rolle: „Planung“ statt „Einkauf“" in warnings[0].text()
    assert "Telefon: „123“ statt „456“" in warnings[0].text()
    dialog.close()


def test_contact_suggestion_without_matching_manual_contact_has_no_warning(application):
    customer = Customer(
        id=7,
        display_name="Muster GmbH",
        contacts=(Contact(name="Ada", email="ada@example.test"),),
    )
    proposed = Contact(name="Bea", role="Einkauf", phone="555")
    suggestion = CustomerSuggestion(
        id=5,
        customer_id=7,
        field_name="contact_name",
        value=proposed.name,
        source=SourcePath("archive", "2026/Muster/Mail.txt"),
        fingerprint="different-contact",
        suggestion_type="contact",
        contact=proposed,
    )

    dialog = CustomerDataSuggestionsDialog(
        (suggestion,), 3, customer=customer
    )
    assert not dialog.findChildren(QLabel, "PopupWarning")
    assert {"Rolle: Einkauf", "E-Mail: -", "Telefon: 555"}.issubset(
        _label_texts(dialog)
    )
    dialog.close()
