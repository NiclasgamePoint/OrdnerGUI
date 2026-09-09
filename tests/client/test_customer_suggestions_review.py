"""Synthetic review data only; no repository snapshots or customer documents."""

from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QFrame, QMessageBox

from papagui_contracts import Contact, Customer, SourcePath
from papagui_contracts.recognition import CustomerSuggestion
from papagui_client.adapters.http_api import (
    ApiConflictError,
    ApiIdempotencyConflictError,
    ApiRejectedError,
    ApiUnavailableError,
)
from papagui_client.adapters.http_review import HttpReviewGateway, SuggestionPage
from papagui_client.adapters.sqlite_customer_snapshot import SQLiteCustomerSnapshot
from papagui_client.gui.customer_detail import CustomerDetailWidget
from papagui_client.gui.customer_editor import CustomerEditorDialog
from papagui_client.gui.customer_suggestions import CustomerSuggestionsDialog, recognition_text
from papagui_client.gui.main import ClientMainWindow
from papagui_client.gui.widgets.buttons import AppButton
from papagui_client.presentation.coordinators import NavigationCoordinator
from tests.client.test_customer_detail_and_recognition import _MainCustomers, _MainSearch, _MainSync


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def suggestion(identifier=1, **kwargs):
    return CustomerSuggestion(
        id=identifier,
        customer_id=7,
        field_name="email",
        value=f"person{identifier}@example.test",
        source=SourcePath("synthetic", "example.pdf"),
        fingerprint=f"synthetic-{identifier}",
        **kwargs,
    )


def settle(window, application):
    for _ in range(10):
        assert window._pool.waitForDone(2_000)
        application.processEvents()
        if not window._tasks:
            return
    pytest.fail("Background requests did not settle")


def test_page_wire_includes_groups_and_reject_reason():
    gateway = HttpReviewGateway("http://server")
    gateway._transport = Mock()
    gateway._transport.json.return_value = {
        "suggestions": [suggestion().to_dict()],
        "revision": 3,
        "total": 12,
        "has_more": True,
        "recognition": {"state": "partial"},
    }
    page = gateway.customer_suggestions_page(7, limit=3, offset=3)
    assert page.total == 12 and page.has_more and page.recognition["state"] == "partial"
    assert "include_groups=true" in gateway._transport.json.call_args.args[1]
    assert "offset=3" in gateway._transport.json.call_args.args[1]
    gateway._transport.json.return_value = {
        "suggestions": [suggestion(index).to_dict() for index in range(1, 8)],
        "revision": 3,
    }
    legacy_page = gateway.customer_suggestions_page(7, limit=3, offset=3)
    assert [item.id for item in legacy_page.suggestions] == [4, 5, 6]
    assert legacy_page.total == 7 and legacy_page.has_more
    gateway._transport.json.return_value = {
        "suggestion": suggestion().to_dict(),
        "customer": Customer(id=7, revision=4).to_dict(),
    }
    gateway.decide_suggestion(
        7, 1, "reject", 3, reason="wrong_customer", idempotency_key="test-key"
    )
    assert gateway._transport.json.call_args.args[2]["reason"] == "wrong_customer"
    assert gateway._transport.json.call_args.args[3]["If-Match"] == "3"
    gateway._transport.json.return_value = {"job": {"id": 1, "state": "queued"}}
    assert gateway.start_customer_recognition(7, "extract")["job"]["id"] == 1
    assert gateway._transport.json.call_args.args[2] == {"mode": "extract"}
    gateway._transport.json.return_value = {"state": "running"}
    assert gateway.recognition_status(7)["state"] == "running"
    with pytest.raises(ValueError):
        gateway.start_customer_recognition(7, "other")
    with pytest.raises(ValueError):
        gateway.customer_suggestions_page(7, limit=0)
    gateway._transport.json.return_value = {"revision": 3}
    with pytest.raises(ApiUnavailableError):
        gateway.customer_suggestions_page(7)


def test_bounded_cards_grouping_evidence_and_pagination(application):
    first = suggestion(
        quality="strong",
        evidence_count=9,
        evidence=tuple(
            {
                "source": SourcePath("synthetic", f"file{index}.pdf").to_dict(),
                "page": index + 1,
                "excerpt": "Synthetic evidence",
            }
            for index in range(9)
        ),
    )
    dialog = CustomerSuggestionsDialog((first, *(suggestion(index) for index in range(2, 31))), 3)
    dialog.set_data(dialog._suggestions, 3, total=120, has_more=True)
    assert len(dialog.findChildren(QFrame, "CustomerSuggestionCard")) == 3
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Ergänzungen" in texts
    assert "Gute Belege · 9 Beleg(e)" in texts
    assert not any("Sicherheit:" in text for text in texts)
    sources = []
    dialog.sourceRequested.connect(sources.append)
    next(
        button for button in dialog.findChildren(AppButton) if button.text().startswith("Quelle:")
    ).click()
    assert sources[0].source_id == "synthetic"
    next(
        button
        for button in dialog.findChildren(AppButton)
        if button.text().startswith("Weitere Belege")
    ).click()
    assert any("file3.pdf" in button.text() for button in dialog.findChildren(AppButton))
    for _ in range(9):
        dialog._show_more()
    assert len(dialog.findChildren(QFrame, "CustomerSuggestionCard")) == 30
    pages = []
    dialog.pageRequested.connect(pages.append)
    dialog._show_more()
    assert pages == [30] and dialog._busy
    dialog._show_more()
    assert pages == [30]
    dialog.close()


def test_manual_conflict_and_address_are_one_card(application):
    address = replace(
        suggestion(),
        suggestion_type="address",
        field_name="address",
        payload={"street": "Teststraße 2", "postal_code": "12345", "city": "Teststadt"},
    )
    dialog = CustomerSuggestionsDialog(
        (address,), 4, customer=Customer(id=7, street="Andere Straße 1")
    )
    assert len(dialog.findChildren(QFrame, "CustomerSuggestionCard")) == 1
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Konflikte" in texts
    assert any("aktuell „Andere Straße 1“" in text for text in texts)
    events = []
    dialog.decisionRequested.connect(lambda *args: events.append(args))
    dialog._decide(address, "accept")
    assert not events
    manual = []
    dialog.manualResolutionRequested.connect(lambda: manual.append(True))
    next(
        button for button in dialog.findChildren(AppButton) if button.text() == "Manuell bearbeiten"
    ).click()
    assert manual == [True]
    dialog.close()


def test_live_dialog_uses_returned_revision_for_multiple_decisions(application, monkeypatch):
    current = Customer(id=7, revision=3, display_name="Synthetic customer")
    values = [suggestion(1), suggestion(2)]
    calls = []

    class Review:
        def customer_suggestions_page(self, *args, **kwargs):
            return SuggestionPage(
                tuple(values), current.revision, len(values), recognition={"state": "complete"}
            )

        def decide_suggestion(self, customer_id, suggestion_id, action, revision, **kwargs):
            nonlocal current
            calls.append((suggestion_id, action, revision, kwargs))
            assert revision == current.revision
            item = next(item for item in values if item.id == suggestion_id)
            values.remove(item)
            current = replace(current, revision=revision + 1)
            return item, current

    container = SimpleNamespace(
        settings=SimpleNamespace(server_url="http://server", data_root="/tmp/client"),
        sync=_MainSync(),
        search=_MainSearch(),
        customers=_MainCustomers(),
        navigation=NavigationCoordinator(),
        review_gateway=Review(),
    )
    window = ClientMainWindow(container, automatic_sync=False)
    settle(window, application)
    detail = window.customer_detail
    detail.set_customer(current)
    dialog = CustomerSuggestionsDialog(values, 3, customer=current)
    detail._suggestion_dialog = dialog
    dialog.decisionRequested.connect(window.decide_customer_suggestion)
    dialog._decide(values[0], "accept")
    assert dialog._busy
    # Repeated click during a write cannot produce a second request.
    dialog._decide(values[0], "accept")
    settle(window, application)
    assert dialog._revision == 4 and len(dialog._suggestions) == 1
    box = dialog.findChild(QComboBox)
    box.setCurrentIndex(box.findData("wrong_customer"))
    dialog._decide(dialog._suggestions[0], "reject")
    settle(window, application)
    assert [(call[0], call[2]) for call in calls] == [(1, 3), (2, 4)]
    assert calls[1][3] == {"reason": "wrong_customer"}
    assert dialog._revision == 5 and not dialog._suggestions
    assert window._selected_customer().customer.revision == 5
    assert "Keine offenen Entscheidungen" in dialog.status_label.text()
    detail._suggestion_dialog = None
    dialog.close()
    window.close()


def test_server_job_pagination_failures_and_stale_responses(application, monkeypatch):
    review = SimpleNamespace(
        customer_suggestions_page=Mock(
            return_value=SuggestionPage(
                (suggestion(),), 3, 60, True, recognition={"state": "complete"}
            )
        ),
        recognition_status=Mock(return_value={"state": "partial"}),
        start_customer_recognition=Mock(return_value={"job": {"id": 4, "state": "queued"}}),
    )
    container = SimpleNamespace(
        settings=SimpleNamespace(server_url="http://server", data_root="/tmp/client"),
        sync=_MainSync(),
        search=_MainSearch(),
        customers=_MainCustomers(),
        navigation=NavigationCoordinator(),
        review_gateway=review,
        paths=SimpleNamespace(resolve=Mock(return_value="/tmp/synthetic.pdf")),
    )
    window = ClientMainWindow(container, automatic_sync=False)
    settle(window, application)
    assert window.customer_detail._recognition_status["state"] == "partial"
    assert review.recognition_status.called
    review.recognition_status.return_value = {"state": "complete"}
    review.customer_suggestions_page.return_value = SuggestionPage(
        (suggestion(31),), 4, 60, False, recognition={"state": "complete"}
    )
    window.load_suggestion_page(30)
    settle(window, application)
    assert window.customer_detail._suggestion_offset == 30
    assert window.customer_detail._suggestions[0].id == 31
    assert review.customer_suggestions_page.call_args.kwargs == {"limit": 30, "offset": 30}
    review.customer_suggestions_page.side_effect = OSError("Seite nicht erreichbar")
    window.load_suggestion_page(0)
    settle(window, application)
    assert "Seite nicht erreichbar" in window.customer_detail.recognition_status_label.text()
    assert window.customer_detail._suggestions[0].id == 31
    window.start_customer_recognition("extract")
    settle(window, application)
    assert review.start_customer_recognition.call_args.args == (7, "extract")
    assert window._recognition_poll.isActive()
    assert window.customer_detail._recognition_status["state"] == "running"
    window._suggestion_write_busy = True
    window._poll_customer_recognition()
    window.start_customer_recognition("reassess")
    window.load_suggestion_page(0)
    assert review.start_customer_recognition.call_count == 1
    window._suggestion_write_busy = False
    window._recognition_poll.stop()
    review.customer_suggestions_page.side_effect = None
    window._poll_customer_recognition()
    settle(window, application)
    assert window.customer_detail._recognition_status["state"] == "complete"
    review.start_customer_recognition.side_effect = ApiUnavailableError("Server offline")
    window.start_customer_recognition("reassess")
    settle(window, application)
    assert "Server offline" in window.customer_detail.recognition_status_label.text()
    opened = []
    monkeypatch.setattr(window, "open_native_file", opened.append)
    window.open_suggestion_source(SourcePath("synthetic", "example.pdf"))
    assert opened == ["/tmp/synthetic.pdf"]
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    container.paths.resolve.side_effect = OSError("Quelle nicht verbunden")
    window.open_suggestion_source(SourcePath("synthetic", "example.pdf"))
    assert warnings
    old_request = window._customer_detail_request_id
    window._show_customer_detail(-1)
    window._apply_suggestion_page((7, old_request, 0, None, None))
    window._apply_recognition_start((7, old_request, None, None))
    window._apply_suggestion_decision((7, old_request, 1, None, None))
    assert not window.customer_detail._suggestions
    window.close()


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"error": {"code": "idempotency_conflict"}}, ApiIdempotencyConflictError),
        ({"current": Customer(id=7, revision=4).to_dict()}, ApiConflictError),
    ],
)
def test_gateway_preserves_typed_conflicts(payload, expected):
    gateway = HttpReviewGateway("http://server")
    gateway._transport = Mock()
    gateway._transport.json.side_effect = ApiRejectedError(409, "conflict", payload)
    with pytest.raises(expected):
        gateway.decide_suggestion(7, 1, "accept", 3)
    gateway._transport.json.side_effect = ApiRejectedError(403, "forbidden")
    with pytest.raises(ApiRejectedError):
        gateway.decide_suggestion(7, 1, "accept", 3)
    gateway._transport.json.side_effect = None
    gateway._transport.json.return_value = {}
    with pytest.raises(ApiUnavailableError):
        gateway.decide_suggestion(7, 1, "accept", 3)
    gateway._transport.json.side_effect = OSError("offline")
    with pytest.raises(OSError):
        gateway.customer_suggestions_page(7)


def test_snapshot_contact_payload_independent_evidence_and_stable_ids(tmp_path):
    path = tmp_path / "synthetic-contacts.db"
    contact = Contact(name="Synthetic Person", email="synthetic@example.test", id="synthetic-id")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE customers(id INTEGER,revision INTEGER,display_name TEXT)")
        connection.execute("INSERT INTO customers VALUES(7,3,'Synthetic')")
        connection.execute(
            "CREATE TABLE contacts(id INTEGER,customer_id INTEGER,uid TEXT,name TEXT,role TEXT,email TEXT,phone TEXT)"
        )
        connection.execute(
            "INSERT INTO contacts VALUES(1,7,'synthetic-id','Synthetic Person','','synthetic@example.test','')"
        )
        connection.execute(
            "CREATE TABLE customer_document_suggestions(id INTEGER,customer_id INTEGER,kind TEXT,value TEXT,source_path TEXT,fingerprint TEXT,status TEXT,suggestion_type TEXT,payload_json TEXT)"
        )
        connection.execute(
            "INSERT INTO customer_document_suggestions VALUES(1,7,'contact_name','Synthetic Person','source://synthetic/contact.pdf','synthetic-contact','pending','contact',?)",
            (json.dumps(contact.to_dict()),),
        )
        connection.execute(
            "CREATE TABLE candidate_evidence(id INTEGER,candidate_id INTEGER,source_path TEXT,document_hash TEXT,locator_json TEXT,excerpt TEXT,active INTEGER)"
        )
        for index in (1, 2):
            connection.execute(
                "INSERT INTO candidate_evidence VALUES(?,1,?,'same-hash','{\"page\":1}','Synthetic contact block',1)",
                (index, f"source://synthetic/copy{index}.pdf"),
            )
    snapshot = SQLiteCustomerSnapshot(path)
    assert snapshot.get_customer(7).contacts[0].id == "synthetic-id"
    page = snapshot.customer_suggestions_page(7)
    assert page.suggestions[0].contact == contact
    assert page.suggestions[0].evidence_count == 1
    assert len(page.suggestions[0].evidence) == 2
    with pytest.raises(ValueError, match="nicht vorhanden"):
        snapshot.customer_suggestions_page(8)
    with pytest.raises(ValueError, match="invalid suggestion page"):
        snapshot.customer_suggestions_page(7, offset=-1)


def test_error_does_not_erase_candidates_or_claim_success(application):
    widget = CustomerDetailWidget()
    widget.set_customer(Customer(id=7, revision=3))
    widget.set_suggestions((suggestion(),), 3, total=9, recognition={"state": "partial"})
    dialog = CustomerSuggestionsDialog((suggestion(),), 3)
    widget._suggestion_dialog = dialog
    widget.set_suggestions_error("Verbindung unterbrochen", 3)
    assert widget._suggestion_total == 9 and len(widget._suggestions) == 1
    assert "Verbindung unterbrochen" in dialog.status_label.text()
    assert "Keine offenen" not in dialog.status_label.text()
    widget.set_suggestions((), 3, recognition={"state": "not_evaluated"})
    assert "noch nicht geprüft" in dialog.status_label.text()
    assert "teilweise" in recognition_text({"state": "partial"})
    assert recognition_text({"summary": "Keine unterstützten Dokumente zugeordnet"}).startswith(
        "Keine unterstützten"
    )
    widget._suggestion_dialog = None
    dialog.close()
    widget.close()


def test_offline_immutable_snapshot_and_missing_schema(tmp_path, application):
    path = tmp_path / "synthetic.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE customers(id INTEGER, revision INTEGER)")
        connection.execute("INSERT INTO customers VALUES(7, 3)")
        connection.execute(
            "CREATE TABLE customer_document_suggestions(id INTEGER,customer_id INTEGER,kind TEXT,value TEXT,source_path TEXT,fingerprint TEXT,status TEXT,suggestion_type TEXT,lifecycle TEXT,canonical_id INTEGER,quality TEXT,reasons_json TEXT,payload_json TEXT)"
        )
        connection.execute(
            "INSERT INTO customer_document_suggestions VALUES(1,7,'email','synthetic@example.test','source://synthetic/test.pdf','synthetic-1','pending','field','active',NULL,'strong','[]','{}')"
        )
        connection.execute(
            "INSERT INTO customer_document_suggestions VALUES(2,7,'email','synthetic@example.test','source://synthetic/copy.pdf','synthetic-2','pending','field','active',1,'strong','[]','{}')"
        )
        connection.execute(
            "CREATE TABLE customer_recognition_status(customer_id INTEGER,state TEXT,counts_json TEXT)"
        )
        connection.execute(
            "INSERT INTO customer_recognition_status VALUES(7,'partial',?)",
            (json.dumps({"open": 1}),),
        )
    before = path.read_bytes()
    gateway = HttpReviewGateway("http://server", snapshot=SQLiteCustomerSnapshot(path))
    gateway._transport = Mock()
    gateway._transport.json.side_effect = ApiUnavailableError("offline")
    page = gateway.customer_suggestions_page(7)
    assert page.offline and page.total == 1 and page.recognition["state"] == "partial"
    assert path.read_bytes() == before
    dialog = CustomerSuggestionsDialog(page.suggestions, page.revision)
    dialog.set_data(page.suggestions, page.revision, offline=True, recognition=page.recognition)
    assert "Offline" in dialog.status_label.text()
    assert not dialog.rescan_button.isEnabled()
    assert not next(
        button for button in dialog.findChildren(AppButton) if button.text() == "Annehmen"
    ).isEnabled()
    dialog.close()
    empty = tmp_path / "old.db"
    with sqlite3.connect(empty):
        pass
    with pytest.raises(ValueError, match="keine Vorschlagsprüfung"):
        SQLiteCustomerSnapshot(empty).customer_suggestions_page(7)


def test_contact_id_survives_editor_reordering(application):
    first = Contact(name="Alpha", id="00000000-0000-4000-8000-000000000001")
    second = Contact(name="Beta", id="00000000-0000-4000-8000-000000000002")
    dialog = CustomerEditorDialog(
        Customer(id=7, display_name="Synthetic", contacts=(first, second))
    )
    dialog.contacts_table.removeRow(0)
    assert dialog._read_contacts()[0].id == second.id
    dialog.contacts_table.item(0, 0).setText("Beta korrigiert")
    assert dialog._read_contacts()[0].id == second.id
    dialog.close()


def test_revision_conflict_keeps_card_and_refreshes_current_customer(application, monkeypatch):
    current = Customer(id=7, revision=6, email="current@example.test")
    review = SimpleNamespace(
        customer_suggestions=lambda *args: ((suggestion(),), 3),
        decide_suggestion=Mock(side_effect=ApiConflictError(current)),
    )
    container = SimpleNamespace(
        settings=SimpleNamespace(server_url="http://server", data_root="/tmp/client"),
        sync=_MainSync(),
        search=_MainSearch(),
        customers=_MainCustomers(),
        navigation=NavigationCoordinator(),
        review_gateway=review,
    )
    window = ClientMainWindow(container, automatic_sync=False)
    settle(window, application)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    window.decide_customer_suggestion(suggestion(), "accept", 3)
    settle(window, application)
    assert window.customer_detail._suggestion_revision == 6
    assert len(window.customer_detail._suggestions) == 1
    assert window._selected_customer().customer.email == "current@example.test"
    assert not window._suggestion_write_busy
    window.close()


@pytest.mark.parametrize("offset", [0, 30])
def test_decision_refreshes_all_resolved_cards_and_recovers_an_empty_last_page(application, offset):
    original = Customer(id=7, revision=3, display_name="Synthetic")
    first, duplicate, remaining = suggestion(1), suggestion(2), suggestion(9)
    decided = False
    page_requests = []

    def read_page(*args, **kwargs):
        page_offset = kwargs.get("offset", 0)
        page_requests.append(page_offset)
        if decided:
            values = () if page_offset else (remaining,)
            return SuggestionPage(values, 4, 1, recognition={"state": "complete"})
        return SuggestionPage((first, duplicate), 3, offset + 2, recognition={"state": "complete"})

    def decide(*args, **kwargs):
        nonlocal decided
        decided = True
        return first, replace(original, revision=4)

    review = SimpleNamespace(customer_suggestions_page=read_page, decide_suggestion=decide)
    container = SimpleNamespace(
        settings=SimpleNamespace(server_url="http://server", data_root="/tmp/client"),
        sync=_MainSync(),
        search=_MainSearch(),
        customers=_MainCustomers(),
        navigation=NavigationCoordinator(),
        review_gateway=review,
    )
    window = ClientMainWindow(container, automatic_sync=False)
    settle(window, application)
    detail = window.customer_detail
    detail.set_customer(original)
    detail.set_suggestions((first, duplicate), 3, offset=offset, total=offset + 2)
    dialog = CustomerSuggestionsDialog((first, duplicate), 3, customer=original)
    detail._suggestion_dialog = dialog
    detail._update_suggestion_dialog()
    dialog.decisionRequested.connect(window.decide_customer_suggestion)
    dialog._decide(first, "accept")
    settle(window, application)
    assert detail._suggestion_total == 1
    assert detail._suggestion_offset == 0
    assert [item.id for item in dialog._suggestions] == [9]
    assert dialog._revision == 4 and not dialog._busy
    if offset:
        assert page_requests[-2:] == [30, 0]
    detail._suggestion_dialog = None
    dialog.close()
    window.close()


def test_incomplete_address_opens_manual_editor_instead_of_invalid_accept(application):
    address = replace(suggestion(), suggestion_type="address", field_name="address",
                      payload={"street": "Beispielweg 12", "city": "", "postal_code": "",
                               "address_kind": "incomplete"},
                      reasons=("incomplete-address-block",))
    dialog = CustomerSuggestionsDialog((address,), 1, customer=Customer(id=7))
    manual, decisions = [], []
    dialog.manualResolutionRequested.connect(lambda: manual.append(True))
    dialog.decisionRequested.connect(lambda *args: decisions.append(args))
    buttons = dialog.findChildren(AppButton)
    assert not any(button.text() == "Annehmen" for button in buttons)
    next(button for button in buttons if button.text() == "Manuell bearbeiten").click()
    assert manual == [True] and not decisions
    dialog.close()
