from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint
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
from papagui_client.gui.customer_detail import (
    CustomerDataSuggestionsDialog,
    CustomerDetailWidget,
    JournalEditorDialog,
)
from papagui_client.gui.customer_editor import CustomerEditorDialog
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


def test_recognition_review_offline_empty_and_error_edges(application, monkeypatch):
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
        def recognition_cases(self, _status):
            return ()

        def recognition_runs(self, _limit):
            return ()

    control = EmptyControl()
    empty = RecognitionReviewDialog(control)
    assert "Keine offenen" in empty.details.toPlainText()
    empty.start_run()
    assert control.started == 1
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


def test_restored_customer_cards_inline_actions_and_portable_projects(
    application, monkeypatch
):
    widget = CustomerDetailWidget()
    customer = _customer()
    customer_events = []
    journal_events = []
    folder_events = []
    widget.customerSaveRequested.connect(customer_events.append)
    widget.journalCreateRequested.connect(journal_events.append)
    widget.folderActivated.connect(folder_events.append)

    widget.set_customer(
        customer,
        {
            "archive:2026/Beratung/Muster": {
                "local_path": "/mnt/archive/2026/Beratung/Muster",
                "file_count": 4,
                "last_modified": "2026-04-05T12:30:00",
            }
        },
    )
    assert widget.objectName() == "CustomerPage"
    assert widget.splitter.count() == 2
    assert widget.customer_tabs.tabText(0) == "Notizen"
    assert widget.customer_tabs.tabText(1) == "Journal"
    assert widget.tags.item(0).text() == "A"
    assert widget.contacts.item(0, 0).text() == "Ada"
    assert widget.projects.count() == 1

    widget._folder_rows[0].activated.emit(widget._folder_rows[0].payload)
    assert folder_events == ["archive:2026/Beratung/Muster"]

    widget.notes.setPlainText("Geändert")
    widget._emit_notes_change()
    assert customer_events[-1].notes == ("Geändert",)
    widget._emit_notes_change()
    assert "gespeichert" not in widget.notes_status.text().casefold()

    widget._create_journal_entry()
    assert "Text" in widget.journal_status.text()
    widget.journal_title_input.setText("Termin")
    widget.journal_input.setPlainText("Besprechung")
    widget._create_journal_entry()
    assert journal_events[-1].title == "Termin"
    assert journal_events[-1].customer_id == 7

    project_without_source = CustomerProject(service_type="Messung")
    widget.set_customer(replace(customer, projects=(project_without_source,)))
    assert "keine Quelle" in widget.projects.item(0).text()
    widget.set_customer(replace(customer, entity_type="Privatperson"))
    assert widget.contacts.isColumnHidden(1)
    widget.close()


def test_restored_journal_cards_and_suggestion_cards_cover_actions(
    application, monkeypatch
):
    widget = CustomerDetailWidget()
    widget.set_customer(_customer())
    journal = JournalEntryView(
        "4",
        CustomerJournalEntry(
            id=4,
            customer_id=7,
            entry_number=2,
            title="Termin",
            body="Text",
            created_at="2026-01-02T03:04:00",
        ),
        CustomerSyncState.CONFLICT,
    )
    widget.set_journal((journal,))
    assert "2026" in widget.format_journal_date("2026-01-02T03:04:00")
    assert widget.format_journal_date("") == "-"
    assert widget.format_journal_date("bad") == "bad"

    class FakeMenu:
        choice = "Bearbeiten"

        def __init__(self, _parent):
            self.actions = []

        def addAction(self, text):
            action = SimpleNamespace(text=text)
            self.actions.append(action)
            return action

        def exec(self, _position):
            if self.choice is None:
                return None
            return next(item for item in self.actions if item.text == self.choice)

    edits = []
    deletes = []
    widget.journalEditRequested.connect(edits.append)
    widget.journalDeleteRequested.connect(deletes.append)
    monkeypatch.setattr("papagui_client.gui.customer_detail.QMenu", FakeMenu)
    card = widget._journal_cards[0]
    card._open_context_menu(QPoint())
    assert edits == [journal]
    FakeMenu.choice = "Löschen"
    card._open_context_menu(QPoint())
    assert deletes == [journal]
    FakeMenu.choice = None
    card._open_context_menu(QPoint())

    suggestion = _suggestion()
    contact_suggestion = replace(
        suggestion,
        id=5,
        suggestion_type="contact",
        contact=Contact("Bea", "Einkauf", "bea@example.test", "555"),
    )
    events = []
    dialog = CustomerDataSuggestionsDialog((suggestion, contact_suggestion), 3)
    dialog.decisionRequested.connect(
        lambda value, action, revision: events.append((value.id, action, revision))
    )
    dialog._decide(suggestion, "accept")
    assert events == [(4, "accept", 3)]
    dialog.close()
    empty = CustomerDataSuggestionsDialog((), 0)
    empty.close()

    widget.set_suggestions((suggestion,), 3)
    opened = []

    class SuggestionDialog:
        def __init__(self, *_args):
            self.decisionRequested = SimpleNamespace(connect=lambda callback: opened.append(callback))

        def exec(self):
            opened.append("exec")

    monkeypatch.setattr(
        "papagui_client.gui.customer_detail._CustomerSuggestionsDialog",
        SuggestionDialog,
    )
    widget._open_suggestions()
    assert opened[-1] == "exec"
    widget.set_suggestions((), 0)
    widget._open_suggestions()
    widget.close()


def test_restored_customer_editor_four_tabs_validation_and_read_only_projects(
    application, monkeypatch
):
    no_source = CustomerProject(service_type="Messung", year=2025)
    original = replace(_customer(), projects=(_customer().projects[0], no_source))
    dialog = CustomerEditorDialog(original, explanation="Konflikt manuell zusammenführen")
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "Stammdaten",
        "Kontakte",
        "Notiz",
        "Dateien",
    ]
    assert dialog.selected_folders_table.rowCount() == 2
    assert not dialog.selected_folders_table.editTriggers()
    assert not dialog.found_folders_table.isEnabled()
    assert dialog._project_source(no_source) == ""

    dialog.entity_type.setCurrentText("Privatperson")
    assert dialog.contacts_table.isColumnHidden(1)
    dialog._append_contact(Contact("Neu", phone="99"))
    dialog.contacts_table.setCurrentCell(dialog.contacts_table.rowCount() - 1, 0)
    dialog._remove_contact()
    dialog.contacts_table.setCurrentCell(-1, -1)
    dialog._remove_contact()

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    blank = CustomerEditorDialog()
    blank.company.clear()
    blank.display_name.clear()
    blank._save()
    assert warnings

    dialog.company.setText("Neue GmbH")
    dialog.note_text.clear()
    dialog.service_types.setText("Planung, Beratung")
    changed = dialog.customer()
    assert changed.display_name == "Neue GmbH"
    assert changed.notes == ()
    assert changed.service_types == ("Planung", "Beratung")

    answers = iter((QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: next(answers))
    deleted = []
    dialog.deleteRequested.connect(deleted.append)
    dialog._request_delete()
    dialog._request_delete()
    assert deleted == [original]

    legacy = CustomerEditorDialog(
        Customer(display_name="Alt", folder_path="legacy/path")
    )
    assert "legacy/path" in legacy.folder_summary.text()
    assert "legacy/path" in legacy._project_tooltip()
    legacy.close()
    blank.close()
    dialog.close()


def test_restored_recognition_search_selection_and_unavailable_split(
    application, monkeypatch
):
    control = ReviewControl()
    with_id = SimpleNamespace(customer=_customer())
    without_id = SimpleNamespace(customer=Customer(display_name="Lokal"))
    dialog = RecognitionReviewDialog(control, (without_id, with_id))
    assert dialog.splitter.count() == 2
    assert dialog.case_list.objectName() == "RecognitionCaseList"
    assert dialog.assign_button.text() == "Ordner zuordnen"
    assert dialog.together_button.text() == "Gemeinsam neu anlegen"
    assert dialog.ignore_button.text() == "Nicht verarbeiten"
    assert not dialog.separate_button.isEnabled()

    dialog.customer_search.setText("nicht vorhanden")
    assert "Keine passenden" in dialog.customer_list.item(0).text()
    dialog.customer_search.clear()
    assert dialog._select_customer(7)
    assert not dialog._select_customer(999)
    assert dialog._selected_customer_view().customer.id == 7
    dialog._customer_selected(None)

    information = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: information.append(args))
    dialog._separate_not_supported()
    assert information
    dialog.case_list.setCurrentRow(-1)
    dialog._show_case(-1)
    assert dialog.selected_case() is None
    dialog.close()
