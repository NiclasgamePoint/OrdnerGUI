"""Synthetic server-admin UI and HTTP transport coverage; no customer data."""

from __future__ import annotations

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from papagui_client.adapters.http_api import (
    ApiRejectedError,
    ApiUnavailableError,
    HttpServerControlGateway,
)
from papagui_client.gui.recognition_admin import BLOCKLIST_KINDS, RecognitionAdminWidget
from papagui_client.gui.theme import build_stylesheet
from papagui_client.gui.tray import ServerTrayWindow
from papagui_contracts import DocumentWorkerStatus


def _entry(entry_id=1, kind="email", value="synthetic@example.test", reason="Testeintrag"):
    return {
        "id": entry_id,
        "kind": kind,
        "value": value,
        "reason": reason,
        "created_at": "2030-01-01T12:00:00Z",
    }


def _job(state="running", error=""):
    return {
        "id": "synthetic-job-1",
        "customer_id": None,
        "mode": "rebuild",
        "state": state,
        "created_at": "2030-01-01T12:00:00Z",
        "finished_at": "2030-01-01T12:01:00Z" if state in {"completed", "error"} else None,
        "error": error,
    }


class _Control:
    def __init__(self):
        self.entries = [_entry()]
        self.job = None
        self.calls = []
        self.threads = []
        self.failure = ""
        self.published = True

    def _record(self, *args):
        self.calls.append(args)
        self.threads.append(threading.get_ident())
        if self.failure:
            raise OSError(self.failure)

    def recognition_blocklist(self):
        self._record("list")
        return tuple(self.entries)

    def recognition_rebuild(self):
        self._record("job")
        return dict(self.job) if self.job else None

    def apply_recognition_blocklist_changes(self, additions, deletions):
        self._record("apply", [dict(entry) for entry in additions], list(deletions))
        previous = list(self.entries)
        next_id = max((entry["id"] for entry in previous), default=0) + 1
        self.entries = [entry for entry in previous if entry["id"] not in deletions]
        for addition in additions:
            existing = next((entry for entry in self.entries
                             if entry["kind"] == addition["kind"]
                             and entry["value"].casefold() == addition["value"].casefold()), None)
            entry_id = existing["id"] if existing else next_id
            if existing is None:
                next_id += 1
            self.entries = [entry for entry in self.entries if entry["id"] != entry_id]
            self.entries.append(_entry(entry_id, **addition))
        return {"entries": tuple(self.entries), "changed": self.entries != previous,
                "published": self.published}

    def start_recognition_rebuild(self):
        self._record("start")
        self.job = _job("queued")
        return dict(self.job)

    def cancel_recognition_rebuild(self, job_id):
        self._record("cancel", job_id)
        return dict(self.job)

    def status(self):
        return {"state": "online", "index": {"state": "running"}}


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _flush(application, widget):
    for _ in range(3):
        assert widget._pool.waitForDone(2_000)
        application.processEvents()
        if not widget._tasks:
            return
    assert not widget._tasks


def _stage_entry(widget, value, reason="Testeintrag", kind="email"):
    widget.kind_combo.setCurrentIndex(widget.kind_combo.findData(kind))
    widget.value_input.setText(value)
    widget.reason_input.setText(reason)
    widget.add_button.click()


def _select_rows(widget, *rows):
    widget.entry_list.clearSelection()
    for row in rows:
        widget.entry_list.selectionModel().select(
            widget.entry_list.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


@pytest.fixture
def make_widget(application):
    widgets = []

    def create(control):
        widget = RecognitionAdminWidget(control)
        widget.set_active(True)
        widget._timer.stop()
        widgets.append(widget)
        return widget

    yield create
    for widget in widgets:
        widget.shutdown()
        _flush(application, widget)
        widget.deleteLater()
    application.processEvents()


@pytest.fixture
def make_window(application):
    windows = []

    def create(control):
        window = ServerTrayWindow(SimpleNamespace(server_control=control))
        window._timer.stop()
        windows.append(window)
        return window

    yield create
    for window in windows:
        window.shutdown()
        _flush(application, window.recognition_admin_page)
        window.close()
        window.deleteLater()
    application.processEvents()


@pytest.mark.parametrize("with_workers", [False, True])
def test_gateway_preserves_optional_worker_status_for_server_and_jobs(monkeypatch, with_workers):
    status = {"state": "online"}
    job = _job()
    if with_workers:
        workers = DocumentWorkerStatus(state="running", worker_limit=4, active_workers=2).to_dict()
        status["document_workers"] = workers
        job["document_workers"] = workers
    responses = iter((status, {"job": job}))
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs:
                        io.BytesIO(json.dumps(next(responses)).encode()))
    gateway = HttpServerControlGateway("http://synthetic-server.test")
    assert gateway.status() == status
    assert gateway.recognition_rebuild() == job


def test_gateway_routes_and_normal_token_authentication(monkeypatch):
    requests = []
    responses = iter((
        {"entries": [_entry()]},
        {"entry": _entry(2)},
        {"deleted": True},
        {"job": None},
        {"job": _job("queued")},
        {"job": _job("cancelled")},
    ))

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 5
        return io.BytesIO(json.dumps(next(responses)).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway("http://synthetic-server.test", "synthetic-token")
    assert gateway.recognition_blocklist() == (_entry(),)
    assert gateway.add_recognition_blocklist_entry("email", "synthetic@example.test")["id"] == 2
    assert gateway.delete_recognition_blocklist_entry(2)
    assert gateway.recognition_rebuild() is None
    assert gateway.start_recognition_rebuild()["state"] == "queued"
    assert gateway.cancel_recognition_rebuild("synthetic/job")["state"] == "cancelled"
    assert [(request.method, request.selector) for request in requests] == [
        ("GET", "/v2/admin/recognition/blocklist"),
        ("POST", "/v2/admin/recognition/blocklist"),
        ("DELETE", "/v2/admin/recognition/blocklist/2"),
        ("GET", "/v2/admin/recognition/rebuild"),
        ("POST", "/v2/admin/recognition/rebuild"),
        ("DELETE", "/v2/admin/recognition/rebuild/synthetic%2Fjob"),
    ]
    assert json.loads(requests[1].data) == {
        "kind": "email", "value": "synthetic@example.test", "reason": ""
    }
    assert all(request.get_header("Authorization") == "Bearer synthetic-token" for request in requests)


@pytest.mark.parametrize("method,args,response", [
    ("recognition_blocklist", (), {}),
    ("recognition_blocklist", (), {"entries": [None]}),
    ("add_recognition_blocklist_entry", ("email", "synthetic@example.test"), {"entry": None}),
    ("delete_recognition_blocklist_entry", (1,), {"deleted": False}),
    ("recognition_rebuild", (), {}),
    ("recognition_rebuild", (), {"job": {}}),
    ("start_recognition_rebuild", (), {"job": None}),
    ("cancel_recognition_rebuild", ("synthetic-job-1",), {"job": {"id": "synthetic-job-1"}}),
])
def test_gateway_rejects_invalid_admin_responses(method, args, response):
    gateway = HttpServerControlGateway("http://synthetic-server.test")
    gateway._transport = Mock()
    gateway._transport.json.return_value = response
    with pytest.raises(ApiUnavailableError):
        getattr(gateway, method)(*args)


def test_blocklist_stages_casefold_duplicates_and_applies_once(application, make_widget):
    control = _Control()
    widget = make_widget(control)
    assert not widget.rebuild_button.isEnabled()
    _flush(application, widget)
    assert widget.entry_list.rowCount() == 1
    assert widget.entry_list.item(0, 0).text() == "E-Mail-Adresse"
    assert widget.entry_list.item(0, 1).text() == "synthetic@example.test"
    assert [widget.kind_combo.itemData(i) for i in range(widget.kind_combo.count())] == [
        kind for kind, _label in BLOCKLIST_KINDS
    ]
    assert "OCR" in widget.rebuild_description.text()
    assert "Entscheidungen bleiben erhalten" in widget.rebuild_description.text()
    assert widget.value_input.maxLength() == 320
    assert widget.reason_input.maxLength() == 500
    assert widget.rebuild_button.isEnabled()
    assert not widget.add_button.isEnabled()
    assert not widget.delete_button.isEnabled()
    assert not widget.apply_button.isEnabled()
    assert not widget.discard_button.isEnabled()
    calls_before_drafts = list(control.calls)

    for value, reason in (("  EXAMPLE.test  ", "Synthetischer Test"),
                          ("example.TEST", "Aktualisierter Test")):
        _stage_entry(widget, value, f" {reason} ", "email_domain")
        assert not widget.add_button.isEnabled()
        assert widget.entry_list.rowCount() == 2
        assert len(widget._pending_additions) == 1
        assert all(entry_id < 0 for entry_id in widget._pending_additions)
        assert control.calls == calls_before_drafts
        assert widget.entry_list.item(1, 2).text() == reason
        assert widget.entry_list.item(1, 4).text() == "Neue Sperre · unbestätigt"
        assert not widget.value_input.text()
        assert not widget.reason_input.text()
        assert widget.apply_button.isEnabled()
        assert not widget.rebuild_button.isEnabled()

    widget.apply_button.click()
    assert not widget.apply_button.isEnabled()
    _flush(application, widget)
    assert control.calls == calls_before_drafts + [("apply", [
        {"kind": "email_domain", "value": "example.TEST", "reason": "Aktualisierter Test"},
    ], [])]
    assert widget.entry_list.rowCount() == 2
    assert widget.entry_list.item(1, 4).text() == "Gespeichert"
    assert not widget._pending_additions
    assert not widget._pending_deletions
    assert not widget.apply_button.isEnabled()
    assert not widget.discard_button.isEnabled()
    assert widget.rebuild_button.isEnabled()
    assert "gespeichert" in widget.status.text()
    assert not any(call[0] == "start" for call in control.calls)
    assert set(control.threads).isdisjoint({threading.get_ident()})


def test_offline_and_failed_apply_preserve_drafts_and_allow_reload(application, make_widget):
    control = _Control()
    control.failure = "synthetic unavailable"
    widget = make_widget(control)
    _flush(application, widget)
    assert "synthetic unavailable" in widget.status.text()
    assert widget.reload_button.isEnabled()
    assert not widget.rebuild_button.isEnabled()
    assert not widget.value_input.isEnabled()

    control.failure = ""
    widget.reload_button.click()
    _flush(application, widget)
    _stage_entry(widget, "second@example.test", "Testeingabe")
    pending = dict(widget._pending_additions)
    control.failure = "synthetic write failure"
    widget.apply_button.click()
    _flush(application, widget)
    assert not widget.value_input.text()
    assert not widget.reason_input.text()
    assert widget._pending_additions == pending
    assert widget.entry_list.rowCount() == 2
    assert widget.entry_list.item(1, 1).text() == "second@example.test"
    assert widget.entry_list.item(1, 2).text() == "Testeingabe"
    assert "synthetic write failure" in widget.status.text()
    assert not widget.apply_button.isEnabled()
    assert not widget.rebuild_button.isEnabled()
    assert widget.discard_button.isEnabled()

    control.failure = ""
    widget.reload_button.click()
    _flush(application, widget)
    assert widget._pending_additions == pending
    assert widget.entry_list.rowCount() == 2
    assert widget.apply_button.isEnabled()
    widget.apply_button.click()
    _flush(application, widget)
    assert not widget._pending_additions
    assert widget.entry_list.rowCount() == 2
    assert widget.rebuild_button.isEnabled()


def test_multiple_additions_and_selected_deletions_use_one_batch(application, make_widget):
    control = _Control()
    control.entries.extend((_entry(2, value="second@example.test"),
                            _entry(3, value="retained@example.test")))
    widget = make_widget(control)
    _flush(application, widget)
    initial_calls = list(control.calls)
    _stage_entry(widget, "new@example.test", "Neue E-Mail")
    _stage_entry(widget, "new-domain.test", "Neue Domain", "email_domain")
    _select_rows(widget, 0, 1)
    widget.delete_button.click()
    assert widget._pending_deletions == {1, 2}
    assert widget.entry_list.rowCount() == 5
    assert all(widget.entry_list.item(row, 4).text() == "Entfernung · unbestätigt"
               for row in (0, 1))
    assert all(widget.entry_list.item(row, 1).font().strikeOut() for row in (0, 1))
    assert "2 neue Sperren" in widget.pending_status.text()
    assert "2 Entfernungen" in widget.pending_status.text()
    assert "(4)" in widget.apply_button.text()
    assert control.calls == initial_calls
    assert len(control.entries) == 3

    widget.apply_button.click()
    _flush(application, widget)
    assert control.calls == initial_calls + [("apply", [
        {"kind": "email", "value": "new@example.test", "reason": "Neue E-Mail"},
        {"kind": "email_domain", "value": "new-domain.test", "reason": "Neue Domain"},
    ], [1, 2])]
    assert {entry["value"] for entry in control.entries} == {
        "retained@example.test", "new@example.test", "new-domain.test",
    }
    assert widget.entry_list.rowCount() == 3
    assert all(widget.entry_list.item(row, 4).text() == "Gespeichert" for row in range(3))
    assert not widget._pending_additions
    assert not widget._pending_deletions
    assert widget.rebuild_button.isEnabled()


def test_removal_can_be_undone_and_all_drafts_discarded_without_server_calls(
    application, make_widget
):
    control = _Control()
    widget = make_widget(control)
    _flush(application, widget)
    initial_calls = list(control.calls)
    _stage_entry(widget, "discarded@example.test")
    _select_rows(widget, 0)
    widget.delete_button.click()
    assert widget._pending_deletions == {1}
    assert widget.delete_button.text() == "Entfernung zurücknehmen"
    widget.delete_button.click()
    assert not widget._pending_deletions
    assert not widget.entry_list.item(0, 1).font().strikeOut()
    assert widget.entry_list.item(0, 4).text() == "Gespeichert"

    _select_rows(widget, 1)
    widget.delete_button.click()
    assert not widget._pending_additions
    assert widget.entry_list.rowCount() == 1
    assert not widget.apply_button.isEnabled()
    _stage_entry(widget, "discarded-again@example.test")
    _select_rows(widget, 0)
    widget.delete_button.click()
    widget.discard_button.click()
    assert not widget._pending_additions
    assert not widget._pending_deletions
    assert widget.entry_list.rowCount() == 1
    assert widget.entry_list.item(0, 4).text() == "Gespeichert"
    assert widget.rebuild_button.isEnabled()
    assert not widget.apply_button.isEnabled()
    assert not widget.discard_button.isEnabled()
    assert control.entries == [_entry()]
    assert control.calls == initial_calls


@pytest.mark.parametrize("other_selection", ["saved", "draft"])
def test_mixed_removal_selection_keeps_existing_deletion_staged(
    application, make_widget, other_selection
):
    control = _Control()
    if other_selection == "saved":
        control.entries.append(_entry(2, value="second@example.test"))
    widget = make_widget(control)
    _flush(application, widget)
    initial_calls = list(control.calls)
    saved_entries = list(control.entries)
    if other_selection == "draft":
        _stage_entry(widget, "mixed-draft@example.test")
    _select_rows(widget, 0)
    widget.delete_button.click()
    assert widget._pending_deletions == {1}

    _select_rows(widget, 0, 1)
    assert widget.delete_button.text() == "Auswahl entfernen"
    widget.delete_button.click()
    expected_deletions = {1, 2} if other_selection == "saved" else {1}
    assert widget._pending_deletions == expected_deletions
    assert not widget._pending_additions
    assert widget.entry_list.rowCount() == len(expected_deletions)
    assert all(widget.entry_list.item(row, 4).text() == "Entfernung · unbestätigt"
               for row in range(widget.entry_list.rowCount()))
    assert all(widget.entry_list.item(row, 1).font().strikeOut()
               for row in range(widget.entry_list.rowCount()))
    assert control.entries == saved_entries
    assert control.calls == initial_calls

    widget.apply_button.click()
    _flush(application, widget)
    assert control.calls == initial_calls + [("apply", [], sorted(expected_deletions))]
    assert control.entries == []
    assert widget.entry_list.rowCount() == 0


def test_failed_publication_clears_saved_drafts_and_retries_empty_batch(
    application, make_widget
):
    control = _Control()
    control.published = False
    widget = make_widget(control)
    _flush(application, widget)
    _stage_entry(widget, "publication@example.test")
    _select_rows(widget, 0)
    widget.delete_button.click()
    widget.apply_button.click()
    _flush(application, widget)
    assert [entry["value"] for entry in control.entries] == ["publication@example.test"]
    assert not widget._pending_additions
    assert not widget._pending_deletions
    assert widget.entry_list.item(0, 4).text() == "Gespeichert"
    assert "gespeichert" in widget.status.text()
    assert "noch nicht" in widget.status.text()
    assert widget.apply_button.text() == "Veröffentlichung wiederholen"
    assert widget.apply_button.isEnabled()
    assert not widget.discard_button.isEnabled()
    assert not widget.rebuild_button.isEnabled()

    widget.reload_button.click()
    _flush(application, widget)
    assert widget.apply_button.text() == "Veröffentlichung wiederholen"
    control.published = True
    widget.apply_button.click()
    _flush(application, widget)
    assert [call for call in control.calls if call[0] == "apply"] == [
        ("apply", [{"kind": "email", "value": "publication@example.test",
                    "reason": "Testeintrag"}], [1]),
        ("apply", [], []),
    ]
    assert not widget._publication_pending
    assert not widget.apply_button.isEnabled()
    assert widget.rebuild_button.isEnabled()
    assert not any(call[0] == "start" for call in control.calls)


def test_fresh_widget_retries_server_publication_with_one_empty_batch(application, make_widget):
    control = _Control()
    control.recognition_blocklist_state = Mock(return_value={
        "entries": tuple(control.entries), "publication_pending": True,
    })
    widget = make_widget(control)
    _flush(application, widget)
    control.recognition_blocklist_state.assert_called_once_with()
    assert control.calls == [("job",)]
    assert widget._publication_pending
    assert not widget._pending_additions
    assert not widget._pending_deletions
    assert widget.entry_list.rowCount() == 1
    assert widget.apply_button.text() == "Veröffentlichung wiederholen"
    assert widget.apply_button.isEnabled()
    assert not widget.discard_button.isEnabled()
    assert not widget.rebuild_button.isEnabled()

    widget.apply_button.click()
    _flush(application, widget)
    assert control.calls == [("job",), ("apply", [], [])]
    assert control.entries == [_entry()]
    assert not widget._publication_pending
    assert not widget.apply_button.isEnabled()
    assert widget.rebuild_button.isEnabled()


def test_reload_clears_resolved_server_publication_flag_and_preserves_drafts(
    application, make_widget
):
    control = _Control()
    state = {"entries": tuple(control.entries), "publication_pending": True}
    control.recognition_blocklist_state = Mock(return_value=state)
    widget = make_widget(control)
    _flush(application, widget)
    assert widget._publication_pending
    _stage_entry(widget, "pending-during-publication@example.test")
    _select_rows(widget, 0)
    widget.delete_button.click()
    pending = dict(widget._pending_additions)

    state["publication_pending"] = False
    widget.reload_button.click()
    _flush(application, widget)
    assert control.recognition_blocklist_state.call_count == 2
    assert not widget._publication_pending
    assert widget._pending_additions == pending
    assert widget._pending_deletions == {1}
    assert widget.entry_list.rowCount() == 2
    assert widget.entry_list.item(0, 4).text() == "Entfernung · unbestätigt"
    assert widget.entry_list.item(1, 4).text() == "Neue Sperre · unbestätigt"
    assert widget.apply_button.text() == "Änderungen bestätigen (2)"
    assert widget.apply_button.isEnabled()
    assert not widget.rebuild_button.isEnabled()
    assert control.calls == [("job",), ("job",)]

    widget.discard_button.click()
    assert not widget.apply_button.isEnabled()
    assert widget.rebuild_button.isEnabled()


@pytest.mark.parametrize("unsupported_status", [None, 404, 405, 501])
def test_older_server_preserves_drafts_without_falling_back_to_individual_writes(
    application, make_widget, unsupported_status
):
    control = _Control()
    widget = make_widget(control)
    _flush(application, widget)
    control.apply_recognition_blocklist_changes = (
        None if unsupported_status is None else
        Mock(side_effect=ApiRejectedError(unsupported_status, "synthetic batch unsupported"))
    )
    _stage_entry(widget, "old-server@example.test")
    _select_rows(widget, 0)
    widget.delete_button.click()
    pending = dict(widget._pending_additions)
    initial_calls = list(control.calls)
    widget.apply_button.click()
    _flush(application, widget)
    assert widget._pending_additions == pending
    assert widget._pending_deletions == {1}
    assert widget.entry_list.rowCount() == 2
    assert control.entries == [_entry()]
    assert control.calls == initial_calls
    assert widget.discard_button.isEnabled()
    assert not widget.rebuild_button.isEnabled()
    if unsupported_status is None:
        assert "aktualisierten Server und Client" in widget.status.text()
    else:
        control.apply_recognition_blocklist_changes.assert_called_once()
        assert "synthetic batch unsupported" in widget.status.text()


def test_rebuild_shows_aggregate_worker_status_and_supports_older_servers(application, make_widget):
    control = _Control()
    control.job = _job("running")
    control.job["document_workers"] = DocumentWorkerStatus(
        state="running", worker_limit=4, active_workers=2, queued_documents=6,
        discovered_documents=20, processed_documents=12, reused_documents=8,
        extracted_documents=4, failed_documents=1,
    ).to_dict()
    widget = make_widget(control)
    _flush(application, widget)
    assert "Worker aktiv: 2 / 4" in widget.job_status.text()
    assert "Warteschlange: 6" in widget.job_status.text()
    assert "Wiederverwendet: 8" in widget.job_status.text()
    assert "Fehler: 1" in widget.job_status.text()
    del control.job["document_workers"]
    widget.poll_rebuild()
    _flush(application, widget)
    assert "Worker" not in widget.job_status.text()
    assert "vollständig neu aufgebaut" in widget.job_status.text()


def test_indexserver_shows_live_workers_during_catalog_and_recognition(application, make_window):
    window = make_window(_Control())
    workers = DocumentWorkerStatus(
        state="running", worker_limit=4, active_workers=2, queued_documents=6,
        discovered_documents=20, processed_documents=12, reused_documents=8,
        extracted_documents=4, failed_documents=1,
    ).to_dict()
    for index_state in ("running", "completed"):
        payload = {"state": "online", "index": {
            "state": index_state, "progress": {"phase": "catalog"}},
            "document_workers": workers,
        }
        window._apply_status_details(payload)
        assert "Dokumentinhalte" in window.content_status_label.text()
        assert "Worker aktiv: 2 / 4" in window.worker_summary_label.text()
        assert "Warteschlange: 6" in window.worker_summary_label.text()
        assert "Verarbeitet: 12" in window.content_detail_label.text()
        assert "Wiederverwendet: 8" in window.content_detail_label.text()
        assert "Fehler: 1" in window.content_detail_label.text()
        assert window.content_progress_bar.maximum() == 0
    workers.update(state="cancelled", active_workers=0, queued_documents=0)
    window._apply_status_details(payload)
    assert "abgebrochen" in window.content_status_label.text()
    assert "Worker aktiv: 0 / 4" in window.worker_summary_label.text()
    del payload["document_workers"]
    window._apply_status_details(payload)
    assert "keine Dokument-Worker-Statistik" in window.worker_summary_label.text()
    assert window.worker_output.toPlainText() == ""
    assert "CPU" in window.resource_profile.toolTip() and "RAM" in window.resource_profile.toolTip()


def test_rebuild_start_poll_cancel_and_terminal_error(application, make_widget):
    control = _Control()
    widget = make_widget(control)
    _flush(application, widget)
    widget.rebuild_button.click()
    _flush(application, widget)
    assert control.calls[-1] == ("start",)
    assert "wartet" in widget.job_status.text()
    assert not widget.rebuild_button.isEnabled()
    assert widget.cancel_button.isEnabled()

    control.job = _job("running")
    widget._timer.timeout.emit()
    _flush(application, widget)
    assert "vollständig neu aufgebaut" in widget.job_status.text()
    assert widget.job_progress.maximum() == 0
    widget.cancel_button.click()
    _flush(application, widget)
    assert control.calls[-1] == ("cancel", "synthetic-job-1")
    assert "Abbruch angefordert" in widget.job_status.text()
    assert not widget.cancel_button.isEnabled()
    widget._timer.timeout.emit()
    _flush(application, widget)
    assert "Abbruch angefordert" in widget.job_status.text()

    control.job = _job("cancelled")
    widget._timer.timeout.emit()
    _flush(application, widget)
    assert "abgebrochen" in widget.job_status.text()
    assert widget.rebuild_button.isEnabled()
    assert not widget.cancel_button.isEnabled()
    control.job = _job("error", "synthetic extraction failure")
    widget._timer.timeout.emit()
    _flush(application, widget)
    assert "fehlgeschlagen" in widget.job_status.text()
    assert "synthetic extraction failure" in widget.job_status.text()
    assert widget.rebuild_button.isEnabled()


@pytest.mark.parametrize("status", [400, 409, 422, 429])
def test_rejected_blocklist_request_keeps_running_job_cancellable(
    application, make_widget, status
):
    control = _Control()
    control.job = _job("running")
    widget = make_widget(control)
    _flush(application, widget)
    control.apply_recognition_blocklist_changes = Mock(
        side_effect=ApiRejectedError(
            status,
            "raw_error_payload",
            {"error": {"code": "resource_busy", "message": "synthetic request rejected"}},
        )
    )
    _stage_entry(widget, "synthetic-retry@example.test")
    pending = dict(widget._pending_additions)
    widget.apply_button.click()
    _flush(application, widget)
    assert "synthetic request rejected" in widget.status.text()
    assert "resource_busy" not in widget.status.text()
    assert widget._pending_additions == pending
    assert widget.entry_list.item(1, 1).text() == "synthetic-retry@example.test"
    assert widget.apply_button.isEnabled()
    assert widget.cancel_button.isEnabled()
    assert widget.entry_list.rowCount() == 2
    control.apply_recognition_blocklist_changes.assert_called_once()
    widget.cancel_button.click()
    _flush(application, widget)
    assert control.calls[-1] == ("cancel", "synthetic-job-1")


@pytest.mark.parametrize("status", [401, 403, 503])
def test_server_or_authorization_errors_disable_mutations(application, make_widget, status):
    control = _Control()
    control.job = _job("running")
    widget = make_widget(control)
    _flush(application, widget)
    control.apply_recognition_blocklist_changes = Mock(
        side_effect=ApiRejectedError(status, "synthetic server rejection")
    )
    _stage_entry(widget, "synthetic-retry@example.test")
    pending = dict(widget._pending_additions)
    widget.apply_button.click()
    _flush(application, widget)
    assert "synthetic server rejection" in widget.status.text()
    assert not widget.add_button.isEnabled()
    assert not widget.apply_button.isEnabled()
    assert not widget.cancel_button.isEnabled()
    assert widget.reload_button.isEnabled()
    assert widget._pending_additions == pending
    assert widget.entry_list.rowCount() == 2


def test_existing_rebuild_survives_widget_close_and_reopen(application, make_widget):
    control = _Control()
    control.job = _job()
    widget = make_widget(control)
    _flush(application, widget)
    assert not widget.rebuild_button.isEnabled()
    assert widget.cancel_button.isEnabled()
    widget.shutdown()
    assert not widget._timer.isActive()
    assert not any(call[0] == "cancel" for call in control.calls)

    reopened = make_widget(control)
    _flush(application, reopened)
    assert not reopened.rebuild_button.isEnabled()
    assert reopened.cancel_button.isEnabled()
    control.job = _job("completed")
    reopened.poll_rebuild()
    _flush(application, reopened)
    assert "abgeschlossen" in reopened.job_status.text()
    assert reopened.rebuild_button.isEnabled()


def test_periodic_poll_does_not_interrupt_blocklist_input(application, make_widget):
    control = _Control()
    widget = make_widget(control)
    _flush(application, widget)
    release = threading.Event()

    def slow_status():
        assert release.wait(2)
        return None

    control.recognition_rebuild = slow_status
    try:
        widget.poll_rebuild()
        assert widget.value_input.isEnabled()
        assert widget.reason_input.isEnabled()
        widget.value_input.setText("synthetic-input@example.test")
    finally:
        release.set()
    _flush(application, widget)
    assert widget.value_input.text() == "synthetic-input@example.test"
    assert widget.add_button.isEnabled()


def test_slow_http_keeps_gui_responsive_and_late_result_ignored(application, make_widget):
    control = _Control()
    release = threading.Event()
    entered = threading.Event()

    def slow_list():
        entered.set()
        assert release.wait(2)
        return tuple(control.entries)

    control.recognition_blocklist = slow_list
    widget = make_widget(control)
    try:
        assert entered.wait(1)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        application.processEvents()
        assert ticks == [True]
        widget.shutdown()
        status_at_close = widget.status.text()
        assert not widget._timer.isActive()
    finally:
        release.set()
    _flush(application, widget)
    assert widget.status.text() == status_at_close
    assert widget.entry_list.rowCount() == 0


@pytest.mark.parametrize("width,height", [(760, 560), (560, 420)])
def test_small_window_keeps_footer_and_scrolled_actions_accessible(
    application, make_widget, width, height
):
    widget = make_widget(_Control())
    widget.setStyleSheet(build_stylesheet("light", "#2db89d"))
    _flush(application, widget)
    widget.resize(width, height)
    widget.show()
    application.processEvents()
    assert widget.width() == width
    assert widget.height() == height
    for button in (widget.reload_button,):
        top_left = button.mapTo(widget, QPoint())
        assert widget.rect().contains(top_left)
        assert widget.rect().contains(top_left + button.rect().bottomRight())
    assert widget.scroll.horizontalScrollBar().maximum() == 0
    for button in (widget.add_button, widget.delete_button, widget.apply_button,
                   widget.discard_button, widget.rebuild_button, widget.cancel_button):
        widget.scroll.ensureWidgetVisible(button)
        application.processEvents()
        top_left = button.mapTo(widget.scroll.viewport(), QPoint())
        assert widget.scroll.viewport().rect().contains(top_left)
        assert widget.scroll.viewport().rect().contains(top_left + button.rect().bottomRight())


def test_indexserver_contains_lazy_recognition_tab_without_modal_or_duplicate_close(
    application, make_window, monkeypatch
):
    control = _Control()
    window = make_window(control)
    page = window.recognition_admin_page
    assert isinstance(page, RecognitionAdminWidget)
    assert not isinstance(page, QDialog)
    assert window.tabs.widget(2) is page
    assert window.tabs.tabText(2) == "Kundenerkennung"
    assert not page._timer.isActive()
    application.processEvents()
    assert control.calls == []

    window.show()
    application.processEvents()
    assert control.calls == []
    monkeypatch.setattr(QDialog, "exec", Mock(side_effect=AssertionError("unexpected modal")))
    window.open_recognition_admin()
    _flush(application, page)
    assert window.tabs.currentWidget() is page
    assert page.isVisible()
    assert not page.isWindow()
    assert control.calls == [("list",), ("job",)]
    assert page._timer.isActive()
    assert page.entry_list.rowCount() == 1
    assert not any(button.text() == "Schließen" for button in page.findChildren(QPushButton))
    assert [
        button.text() for button in window.findChildren(QPushButton)
        if button.text() == "Schließen"
    ] == ["Schließen"]
    assert not any(
        button.text() == "Kundenerkennung verwalten"
        for button in window.tabs.widget(0).findChildren(QPushButton)
    )


def test_selecting_recognition_tab_while_window_hidden_defers_load(application, make_window):
    control = _Control()
    window = make_window(control)
    page = window.recognition_admin_page
    window.tabs.setCurrentWidget(page)
    application.processEvents()
    assert control.calls == []
    assert not page._timer.isActive()

    window.show()
    _flush(application, page)
    assert control.calls == [("list",), ("job",)]
    assert page._timer.isActive()


def test_switching_tabs_preserves_form_selection_and_refreshes_rebuild(
    application, make_window
):
    control = _Control()
    control.job = _job("running")
    window = make_window(control)
    window.show()
    page = window.recognition_admin_page
    window.tabs.setCurrentWidget(page)
    _flush(application, page)
    page.kind_combo.setCurrentIndex(page.kind_combo.findData("email_domain"))
    page.value_input.setText("synthetic-draft.test")
    page.reason_input.setText("Noch nicht gespeichert")
    page.entry_list.selectRow(0)
    assert page.cancel_button.isEnabled()

    window.tabs.setCurrentIndex(1)
    application.processEvents()
    assert not page._timer.isActive()
    calls = list(control.calls)
    page._timer.timeout.emit()
    _flush(application, page)
    assert control.calls == calls

    control.entries.append(_entry(2, value="second@example.test"))
    control.job = _job("completed")
    window.tabs.setCurrentWidget(page)
    _flush(application, page)
    assert window.recognition_admin_page is page
    assert page._timer.isActive()
    assert control.calls == calls + [("list",), ("job",)]
    assert page.entry_list.rowCount() == 2
    assert page._selected_id() == 1
    assert page.kind_combo.currentData() == "email_domain"
    assert page.value_input.text() == "synthetic-draft.test"
    assert page.reason_input.text() == "Noch nicht gespeichert"
    assert page.add_button.isEnabled()
    assert "abgeschlossen" in page.job_status.text()
    assert page.rebuild_button.isEnabled()
    assert not page.cancel_button.isEnabled()


def test_pending_changes_survive_reload_tab_switch_and_window_reopen(application, make_window):
    control = _Control()
    window = make_window(control)
    window.show()
    page = window.recognition_admin_page
    window.tabs.setCurrentWidget(page)
    _flush(application, page)
    _stage_entry(page, "persistent-draft@example.test", "Noch nicht bestätigt")
    _select_rows(page, 0)
    page.delete_button.click()
    pending = dict(page._pending_additions)

    def assert_preserved():
        assert page._pending_additions == pending
        assert page._pending_deletions == {1}
        assert page.entry_list.rowCount() == 3
        assert page.entry_list.item(0, 4).text() == "Entfernung · unbestätigt"
        assert page.entry_list.item(2, 1).text() == "persistent-draft@example.test"
        assert page.entry_list.item(2, 4).text() == "Neue Sperre · unbestätigt"
        assert page._selected_ids() == {1}
        assert page.apply_button.isEnabled()
        assert not page.rebuild_button.isEnabled()
        assert not any(call[0] in {"apply", "start"} for call in control.calls)

    control.entries.append(_entry(2, value="external-update@example.test"))
    page.reload_button.click()
    _flush(application, page)
    assert_preserved()
    window.tabs.setCurrentIndex(1)
    application.processEvents()
    assert not page._timer.isActive()
    window.tabs.setCurrentWidget(page)
    _flush(application, page)
    assert_preserved()
    window.hide()
    application.processEvents()
    assert not page._timer.isActive()
    window.show()
    _flush(application, page)
    assert window.recognition_admin_page is page
    assert_preserved()


def test_hiding_indexserver_pauses_polling_and_reopening_refreshes_same_page(
    application, make_window
):
    control = _Control()
    control.job = _job("running")
    window = make_window(control)
    window.show()
    page = window.recognition_admin_page
    window.tabs.setCurrentWidget(page)
    _flush(application, page)
    page.value_input.setText("synthetic-reopen@example.test")
    window.hide()
    application.processEvents()
    assert not page._timer.isActive()
    calls = list(control.calls)
    page._timer.timeout.emit()
    _flush(application, page)
    assert control.calls == calls
    assert not any(call[0] == "cancel" for call in control.calls)

    control.job = _job("error", "synthetic extraction failure")
    window.show()
    _flush(application, page)
    assert window.tabs.currentWidget() is page
    assert page._timer.isActive()
    assert control.calls == calls + [("list",), ("job",)]
    assert page.value_input.text() == "synthetic-reopen@example.test"
    assert "synthetic extraction failure" in page.job_status.text()
    assert page.rebuild_button.isEnabled()


def test_reopening_during_pending_load_refreshes_after_response(application, make_window):
    control = _Control()
    release = threading.Event()
    entered = threading.Event()

    def delayed_first_list():
        control._record("list")
        entries = tuple(control.entries)
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
        return entries

    control.recognition_blocklist = delayed_first_list
    window = make_window(control)
    page = window.recognition_admin_page
    window.tabs.setCurrentWidget(page)
    window.show()
    try:
        assert entered.wait(1)
        window.hide()
        control.entries.append(_entry(2, value="second@example.test"))
        window.show()
        application.processEvents()
        assert control.calls == [("list",)]
    finally:
        release.set()
    _flush(application, page)
    assert control.calls == [("list",), ("job",), ("list",), ("job",)]
    assert page.entry_list.rowCount() == 2
    assert page.rebuild_button.isEnabled()


def test_offline_recognition_tab_remains_accessible_and_recovers(application, make_window):
    control = _Control()
    control.failure = "synthetic unavailable"
    window = make_window(control)
    window.show()
    window._show_offline("synthetic unavailable")
    page = window.recognition_admin_page
    assert window.tabs.isTabEnabled(window.tabs.indexOf(page))
    window.tabs.setCurrentWidget(page)
    _flush(application, page)
    assert "synthetic unavailable" in page.status.text()
    assert page.reload_button.isEnabled()
    assert not page.rebuild_button.isEnabled()
    assert not page.add_button.isEnabled()
    assert not page.delete_button.isEnabled()

    control.failure = ""
    page.reload_button.click()
    _flush(application, page)
    assert page.entry_list.rowCount() == 1
    assert page.rebuild_button.isEnabled()
    assert page.value_input.isEnabled()
