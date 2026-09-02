from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from papagui_contracts import (
    Contact,
    Customer,
    CustomerJournalEntry,
    CustomerProject,
    SourcePath,
)
from papagui_contracts.recognition import (
    CustomerSuggestion,
    RecognitionCase,
    RecognitionEvidence,
    RecognitionRunSummary,
)
from papagui_client.adapters.http_api import HttpServerControlGateway
from papagui_client.adapters.http_review import HttpReviewGateway
from papagui_client.application.models import CustomerSyncState, JournalEntryView
from papagui_client.gui.customer_detail import CustomerDetailWidget, JournalEditorDialog
from papagui_client.gui.recognition_review import RecognitionReviewDialog
from papagui_client.gui.main import ClientMainWindow
from papagui_client.application.models import (
    CustomerView,
    GlobalSearchHit,
    GlobalSearchKind,
    GlobalSearchRecord,
    JournalReplayResult,
    JournalWriteResult,
)
from papagui_client.presentation.coordinators import NavigationCoordinator


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _customer():
    return Customer(
        id=7,
        revision=3,
        display_name="Muster GmbH",
        company="Muster",
        city="Berlin",
        contacts=(Contact("Ada", "Planung", "ada@example.test", "123"),),
        notes=("Nur nachmittags",),
        tags=("A", "B"),
        projects=(
            CustomerProject(
                id=2,
                customer_id=7,
                source=SourcePath("archive", "2026/Beratung/Muster"),
                service_type="Beratung",
                project_label="Umbau",
                project_city="Berlin",
                year=2026,
                provenance="folder",
            ),
        ),
    )


def _suggestion():
    return CustomerSuggestion(
        4,
        7,
        "email",
        "neu@example.test",
        SourcePath("archive", "2026/Beratung/Muster/Brief.pdf"),
        "fingerprint",
        "Kontakt: neu@example.test",
        "document-email",
        0.91,
    )


def test_customer_detail_renders_collections_and_emits_focused_actions(application):
    widget = CustomerDetailWidget()
    widget.set_customer(_customer())
    journal = JournalEntryView(
        "4",
        CustomerJournalEntry(id=4, customer_id=7, title="Termin", body="Text"),
        CustomerSyncState.PENDING,
    )
    widget.set_journal((journal,))
    widget.set_suggestions((_suggestion(),), 3)

    assert "Muster GmbH" in widget.heading.text()
    assert "archive:2026/Beratung/Muster" in widget.projects.item(0).text()
    assert "Ada" in widget.contacts.item(0).text()
    assert widget.notes.item(0).text() == "Nur nachmittags"
    assert widget.tags.count() == 2
    assert "pending" in widget.journal.item(0).text()
    assert "Provenienz" not in widget.suggestions.item(0).text()
    assert "archive:2026" in widget.suggestions.item(0).text()

    events = []
    widget.journalEditRequested.connect(lambda value: events.append(("edit", value.key)))
    widget.journalDeleteRequested.connect(lambda value: events.append(("delete", value.key)))
    widget.suggestionDecisionRequested.connect(
        lambda value, action, revision: events.append((action, value.id, revision))
    )
    widget.journal.setCurrentRow(0)
    widget._emit_edit()
    widget._emit_delete()
    widget.suggestions.setCurrentRow(0)
    widget._emit_suggestion("accept")
    assert events == [("edit", "4"), ("delete", "4"), ("accept", 4, 3)]

    widget.set_customer(None)
    assert widget.projects.count() == 0


def test_journal_editor_preserves_identity_and_edits_text(application):
    original = CustomerJournalEntry(id=4, customer_id=7, title="Alt", body="Text")
    dialog = JournalEditorDialog(original)
    dialog.title.setText("Neu")
    dialog.body.setPlainText("Inhalt")
    assert dialog.entry().id == 4
    assert dialog.entry().title == "Neu"
    dialog.close()


class ReviewControl:
    admin_unlocked = True

    def __init__(self):
        self.decisions = []
        self.started = 0

    def recognition_cases(self, _status):
        return (
            RecognitionCase(
                "sig-1",
                "muster",
                "Muster GmbH",
                (SourcePath("archive", "2026/Beratung/Muster"),),
                reason="nicht eindeutig",
                evidence=(
                    RecognitionEvidence(
                        "name",
                        "Muster",
                        SourcePath("archive", "2026/Beratung/Muster/Brief.pdf"),
                        "Muster GmbH",
                        "folder-name",
                        0.8,
                    ),
                ),
            ),
        )

    def recognition_runs(self, _limit):
        return (RecognitionRunSummary(id=1, detected=1, pending=1, started_at="now"),)

    def start_recognition(self):
        self.started += 1
        return RecognitionRunSummary(id=2, detected=1, pending=1)

    def decide_recognition(self, *args):
        self.decisions.append(args)


def test_recognition_review_shows_provenance_and_decides_with_revision(
    application, monkeypatch
):
    control = ReviewControl()
    view = SimpleNamespace(customer=_customer())
    dialog = RecognitionReviewDialog(control, (view,))

    assert dialog.cases.count() == 1
    assert "archive:2026/Beratung/Muster" in dialog.details.toPlainText()
    dialog.decide("accept")
    assert control.decisions[-1] == ("sig-1", "accept", None, 0)
    dialog.customer.setCurrentIndex(1)
    dialog.decide("assign")
    assert control.decisions[-1] == ("sig-1", "assign", 7, 3)
    dialog.decide("reject")
    assert control.decisions[-1] == ("sig-1", "reject", None, None)
    dialog.start_run()
    assert control.started == 1

    warnings = []
    dialog.customer.setCurrentIndex(0)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    dialog.decide("assign")
    assert warnings
    dialog.close()


def test_recognition_review_offline_empty_auth_and_error_edges(application, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    class FailingLoad(ReviewControl):
        def recognition_cases(self, _status):
            raise OSError("offline")

    offline = RecognitionReviewDialog(FailingLoad())
    assert "offline" in offline.status.text()
    offline.cases.setCurrentRow(-1)
    offline._show_case(-1)
    offline.decide("reject")
    offline.close()

    class EmptyControl(ReviewControl):
        admin_unlocked = False

        def recognition_cases(self, _status):
            return ()

        def recognition_runs(self, _limit):
            return ()

        def login_admin(self, _password):
            if getattr(self, "fail_login", False):
                raise ValueError("bad password")
            self.admin_unlocked = True

    control = EmptyControl()
    empty = RecognitionReviewDialog(control)
    assert "Keine offenen" in empty.details.toPlainText()
    monkeypatch.setattr(
        "papagui_client.gui.recognition_review.QInputDialog.getText",
        lambda *_args: ("", False),
    )
    empty.start_run()
    assert control.started == 0
    monkeypatch.setattr(
        "papagui_client.gui.recognition_review.QInputDialog.getText",
        lambda *_args: ("secret", True),
    )
    assert empty._ensure_admin()
    control.admin_unlocked = False
    control.fail_login = True
    assert not empty._ensure_admin()

    control.admin_unlocked = True
    control.start_recognition = Mock(side_effect=OSError("run failed"))
    empty.start_run()
    assert any("run failed" in str(args) for args in warnings)
    empty.close()

    failing = ReviewControl()
    dialog = RecognitionReviewDialog(failing)
    failing.decide_recognition = Mock(side_effect=OSError("decision failed"))
    dialog.decide("accept")
    assert any("decision failed" in str(args) for args in warnings)
    dialog.close()


def test_server_control_recognition_and_suggestion_wire_contract():
    gateway = HttpServerControlGateway("http://server")
    gateway._transport = Mock()
    gateway._admin_session = "admin"
    case = ReviewControl().recognition_cases("pending")[0]
    gateway._transport.json.return_value = {"cases": [case.to_dict()]}
    assert gateway.recognition_cases()[0].signature == "sig-1"

    run = RecognitionRunSummary(id=1, detected=1)
    gateway._transport.json.return_value = {"runs": [run.to_dict()]}
    assert gateway.recognition_runs(1)[0].detected == 1
    gateway._transport.json.return_value = {"summary": run.to_dict()}
    assert gateway.start_recognition().id == 1

    decision = {
        "signature": "sig-1",
        "action": "assign",
        "customer_id": 7,
        "decided_at": "now",
    }
    gateway._transport.json.return_value = {
        "decision": decision,
        "customer": _customer().to_dict(),
    }
    value, customer = gateway.decide_recognition("sig/1", "assign", 7, 3)
    assert value.customer_id == customer.id == 7
    call = gateway._transport.json.call_args
    assert call.args[1].endswith("sig%2F1/decision")
    assert call.args[2]["expected_revision"] == 3
    assert "Idempotency-Key" in call.args[3]

    suggestion = _suggestion()
    gateway._transport.json.return_value = {
        "suggestions": [suggestion.to_dict()],
        "revision": 3,
    }
    values, revision = gateway.customer_suggestions(7)
    assert values == (suggestion,) and revision == 3
    gateway._transport.json.return_value = {
        "suggestion": suggestion.to_dict(),
        "customer": _customer().to_dict(),
    }
    accepted, customer = gateway.decide_suggestion(7, 4, "accept", 3)
    assert accepted.id == 4 and customer.id == 7

    public = HttpReviewGateway("http://server")
    public._transport = Mock()
    public._transport.json.return_value = {
        "suggestions": [suggestion.to_dict()],
        "revision": 3,
    }
    assert public.customer_suggestions(7)[0] == (suggestion,)
    public._transport.json.return_value = {
        "suggestion": suggestion.to_dict(),
        "customer": _customer().to_dict(),
    }
    public.decide_suggestion(7, 4, "accept", 3, idempotency_key="suggestion-001")
    headers = public._transport.json.call_args.args[3]
    assert "X-PapaGUI-Admin-Session" not in headers


class _MainSync:
    def start(self, *_args, **_kwargs):
        return None

    def stop(self):
        return None


class _MainSearch:
    def search(self, *_args, **_kwargs):
        return ()


class _MainCustomers:
    def __init__(self):
        self.views = (CustomerView("7", _customer()),)

    def list(self):
        return self.views

    def conflicts(self):
        return ()


class _MainJournals:
    def __init__(self):
        self.calls = []
        self.view = JournalEntryView(
            "4", CustomerJournalEntry(id=4, customer_id=7, title="Alt", body="Text")
        )

    def list(self, _customer_id):
        return (self.view,)

    def save(self, *args, **kwargs):
        self.calls.append(("save", args, kwargs))
        return JournalWriteResult(args[1], False, JournalReplayResult())

    def delete(self, *args):
        self.calls.append(("delete", args))
        return JournalWriteResult(None, False, JournalReplayResult())

    def discard(self, key):
        self.calls.append(("discard", key))


class _MainControl(ReviewControl):
    def customer_suggestions(self, _customer_id, _status):
        return (_suggestion(),), 3

    def decide_suggestion(self, *args):
        self.decisions.append(args)


class _AcceptedJournalDialog:
    class DialogCode:
        Accepted = 1

    def __init__(self, entry=None, parent=None):
        self._entry = entry or CustomerJournalEntry(title="Neu", body="Text")

    def exec(self):
        return 1

    def entry(self):
        return self._entry


def test_main_window_customer_detail_journal_review_and_global_navigation(
    application, monkeypatch
):
    control = _MainControl()
    journals = _MainJournals()
    container = SimpleNamespace(
        settings=SimpleNamespace(server_url="http://server", data_root="/tmp/client"),
        sync=_MainSync(),
        search=_MainSearch(),
        customers=_MainCustomers(),
        journals=journals,
        navigation=NavigationCoordinator(),
        review_gateway=control,
    )
    window = ClientMainWindow(container, automatic_sync=False)
    assert window.customer_detail.projects.count() == 1
    assert window.customer_detail.journal.count() == 1
    assert window.customer_detail.suggestions.count() == 1

    monkeypatch.setattr(
        "papagui_client.gui.main.JournalEditorDialog", _AcceptedJournalDialog
    )
    window.add_journal_entry()
    window.edit_journal_entry(journals.view)
    window.delete_journal_entry(journals.view)
    local = JournalEntryView(
        "local:x", CustomerJournalEntry(customer_id=7, title="Lokal", body="Text")
    )
    window.delete_journal_entry(local)
    assert [call[0] for call in journals.calls] == [
        "save",
        "save",
        "delete",
        "discard",
    ]

    window.decide_customer_suggestion(_suggestion(), "accept", 3)
    assert control.decisions[-1][:4] == (7, 4, "accept", 3)
    hit = GlobalSearchHit(
        GlobalSearchRecord(
            GlobalSearchKind.CUSTOMER,
            "customer:7",
            "Muster GmbH",
            customer_id=7,
        )
    )
    window.tabs.setCurrentIndex(0)
    window._search_result_activated(hit)
    assert window.tabs.currentIndex() == 1
    window._search_result_activated(object())

    assert not hasattr(container, "server_control")
    window.close()
