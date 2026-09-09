"""Asynchronous, server-wide controls for customer recognition."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QItemSelectionModel, Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from papagui_client.adapters.http_api import ApiRejectedError
from papagui_client.presentation.document_workers import document_worker_summary

from .tasks import BackgroundTask
from .widgets.buttons import AppButton


BLOCKLIST_KINDS = (
    ("email", "E-Mail-Adresse"),
    ("phone", "Telefonnummer"),
    ("email_domain", "E-Mail-Domain"),
    ("contact_name", "Kontaktname"),
    ("company", "Firmenname"),
)
_ACTIVE_JOB_STATES = frozenset({"queued", "starting", "running", "cancelling"})
_JOB_LABELS = {
    "queued": "Neuaufbau wartet auf die Verarbeitung",
    "starting": "Neuaufbau wird gestartet",
    "running": "Kundenerkennung wird vollständig neu aufgebaut",
    "cancelling": "Neuaufbau wird abgebrochen",
    "cancelled": "Neuaufbau abgebrochen",
    "completed": "Neuaufbau abgeschlossen",
    "error": "Neuaufbau fehlgeschlagen",
    "failed": "Neuaufbau fehlgeschlagen",
}


class RecognitionAdminWidget(QWidget):
    """Embedded server administration with polling only while its tab is visible."""

    def __init__(self, server_control, parent=None):
        super().__init__(parent)
        self._control = server_control
        # Pending HTTP calls survive hiding the tab. Closing the window must
        # neither wait for nor cancel a server job.
        self._pool = QThreadPool.globalInstance()
        self._tasks: set[BackgroundTask] = set()
        self._entries: list[dict] = []
        self._pending_additions: dict[int, dict] = {}
        self._pending_deletions: set[int] = set()
        self._next_draft_id = -1
        self._publication_pending = False
        self._job: dict | None = None
        self._online = False
        self._loaded = False
        self._busy = False
        self._closed = False
        self._active = False
        self._reload_on_idle = False
        self._operation = ""
        self._cancel_requested_id: str | None = None

        self.setObjectName("RecognitionAdminPage")
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(2_000)
        self._timer.timeout.connect(self.poll_rebuild)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("ThemedScrollContent")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 12, 4, 12)
        layout.setSpacing(12)

        outer_layout = layout
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("ThemedScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)
        self.scroll.setWidget(content)
        outer_layout.addWidget(self.scroll, 1)

        blocklist_title = QLabel("Serverweite Sperrliste")
        blocklist_title.setObjectName("IndexCardTitle")
        layout.addWidget(blocklist_title)
        hint = QLabel(
            "Gesperrte Werte werden bei der automatischen Kundenerkennung für alle "
            "Kunden ausgeschlossen. Bestehende Kundendaten bleiben erhalten. "
            "Es gelten exakte Treffer nach Vereinheitlichung der Schreibweise. "
            "Eine E-Mail-Domain sperrt nur diese Domain, keine Unterdomains. "
            "Sammle Änderungen und bestätige sie anschließend gemeinsam."
        )
        hint.setObjectName("PopupCaption")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.entry_list = QTableWidget(0, 5)
        self.entry_list.setHorizontalHeaderLabels(("Typ", "Wert", "Grund", "Erstellt", "Status"))
        self.entry_list.setAccessibleName("Serverweite Sperrliste der Kundenerkennung")
        self.entry_list.setMinimumHeight(150)
        self.entry_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.entry_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.entry_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.entry_list.verticalHeader().hide()
        self.entry_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.entry_list.itemSelectionChanged.connect(self._update_controls)
        layout.addWidget(self.entry_list, 1)
        self.pending_status = QLabel()
        self.pending_status.setObjectName("PopupCaption")
        self.pending_status.setWordWrap(True)
        layout.addWidget(self.pending_status)

        fields = QFormLayout()
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.kind_combo = QComboBox()
        for kind, label in BLOCKLIST_KINDS:
            self.kind_combo.addItem(label, kind)
        self.value_input = QLineEdit()
        self.value_input.setMaxLength(320)
        self.value_input.setPlaceholderText("Wert eingeben …")
        self.value_input.textChanged.connect(self._update_controls)
        self.reason_input = QLineEdit()
        self.reason_input.setMaxLength(500)
        self.reason_input.setPlaceholderText("Optionaler Grund für die Sperre")
        fields.addRow("Typ", self.kind_combo)
        fields.addRow("Wert", self.value_input)
        fields.addRow("Grund", self.reason_input)
        layout.addLayout(fields)
        entry_actions = QVBoxLayout()
        self.add_button = AppButton("Sperre vormerken", AppButton.SECONDARY)
        self.add_button.clicked.connect(self.add_entry)
        self.delete_button = AppButton("Auswahl entfernen", AppButton.DANGER)
        self.delete_button.clicked.connect(self.delete_entry)
        entry_actions.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        entry_actions.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.apply_button = AppButton("Änderungen bestätigen")
        self.apply_button.clicked.connect(self.apply_changes)
        self.discard_button = AppButton("Vormerkungen verwerfen", AppButton.SECONDARY)
        self.discard_button.clicked.connect(self.discard_changes)
        entry_actions.addWidget(self.apply_button, 0, Qt.AlignmentFlag.AlignLeft)
        entry_actions.addWidget(self.discard_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(entry_actions)

        self.status = QLabel("Serverdaten werden geladen …")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("PopupCaption")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        rebuild_card = QFrame()
        rebuild_card.setObjectName("IndexControlCard")
        rebuild_layout = QVBoxLayout(rebuild_card)
        rebuild_title = QLabel("Vollständiger Neuaufbau")
        rebuild_title.setObjectName("IndexCardTitle")
        rebuild_layout.addWidget(rebuild_title)
        self.rebuild_description = QLabel(
            "Liest alle Dokumente erneut ein, einschließlich Texterkennung (OCR), "
            "und baut die automatischen Vorschläge neu auf. Manuell gepflegte "
            "Kundendaten, Kunden und bisherige Entscheidungen bleiben erhalten. "
            "Der Vorgang läuft auf dem Server weiter, auch wenn dieses Fenster geschlossen wird."
        )
        self.rebuild_description.setWordWrap(True)
        self.rebuild_description.setObjectName("PopupCaption")
        rebuild_layout.addWidget(self.rebuild_description)
        self.job_status = QLabel("Status wird geladen …")
        self.job_status.setTextFormat(Qt.TextFormat.PlainText)
        self.job_status.setWordWrap(True)
        rebuild_layout.addWidget(self.job_status)
        self.job_progress = QProgressBar()
        self.job_progress.setTextVisible(False)
        self.job_progress.hide()
        rebuild_layout.addWidget(self.job_progress)
        rebuild_actions = QVBoxLayout()
        self.rebuild_button = AppButton("Kundenerkennung vollständig neu aufbauen")
        self.rebuild_button.clicked.connect(self.start_rebuild)
        self.cancel_button = AppButton("Neuaufbau abbrechen", AppButton.SECONDARY)
        self.cancel_button.clicked.connect(self.cancel_rebuild)
        rebuild_actions.addWidget(self.rebuild_button, 0, Qt.AlignmentFlag.AlignLeft)
        rebuild_actions.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignLeft)
        rebuild_layout.addLayout(rebuild_actions)
        layout.addWidget(rebuild_card)

        footer = QHBoxLayout()
        self.reload_button = AppButton("Aktualisieren", AppButton.SECONDARY)
        self.reload_button.clicked.connect(self.reload)
        footer.addWidget(self.reload_button)
        footer.addStretch()
        outer_layout.addLayout(footer)
        root.addWidget(body)
        self._update_controls()

    def reload(self) -> None:
        control = self._control
        if control is None:
            self.status.setText("Keine Serversteuerung konfiguriert.")
            return
        load_state = getattr(control, "recognition_blocklist_state", None)
        self._submit(
            "load",
            lambda: (load_state() if callable(load_state) else control.recognition_blocklist(),
                     control.recognition_rebuild()),
            "Sperrliste und Neuaufbau werden geladen …",
        )

    def add_entry(self) -> None:
        if not self.add_button.isEnabled():
            return
        kind = str(self.kind_combo.currentData())
        value = self.value_input.text().strip()
        reason = self.reason_input.text().strip()
        duplicate = next((key for key, entry in self._pending_additions.items()
                          if entry["kind"] == kind and entry["value"].casefold() == value.casefold()), None)
        if duplicate is None:
            if len(self._pending_additions) >= 500:
                self.status.setText("Bitte zuerst die 500 vorgemerkten Sperren bestätigen.")
                return
            duplicate = self._next_draft_id
            self._next_draft_id -= 1
        self._pending_additions[duplicate] = dict(id=duplicate, kind=kind, value=value, reason=reason)
        self.value_input.clear()
        self.reason_input.clear()
        self._show_entries()
        self.status.setText("Sperre vorgemerkt. Mit „Änderungen bestätigen“ gemeinsam speichern.")
        self._update_controls()
        self.value_input.setFocus()

    def delete_entry(self) -> None:
        if not self.delete_button.isEnabled():
            return
        selected = self._selected_ids()
        if selected <= self._pending_deletions:
            future_deletions = self._pending_deletions - selected
        else:
            future_deletions = self._pending_deletions | {
                entry_id for entry_id in selected if entry_id > 0}
        if len(future_deletions) > 500:
            self.status.setText("Bitte höchstens 500 Entfernungen gemeinsam vormerken.")
            return
        self._pending_deletions = future_deletions
        for entry_id in selected:
            self._pending_additions.pop(entry_id, None)
        self._show_entries()
        self.status.setText("Vormerkungen geändert. Gespeichert wird erst nach dem Bestätigen.")
        self._update_controls()

    def apply_changes(self) -> None:
        if not self.apply_button.isEnabled():
            return
        apply = getattr(self._control, "apply_recognition_blocklist_changes", None)
        if apply is None:
            self.status.setText("Gemeinsames Bestätigen benötigt einen aktualisierten Server und Client. "
                                "Deine Vormerkungen bleiben erhalten.")
            return
        additions = [{key: entry[key] for key in ("kind", "value", "reason")}
                     for entry in self._pending_additions.values()]
        deletions = sorted(self._pending_deletions)
        self._submit("apply", lambda: apply(additions, deletions),
                     "Sperrlistenänderungen werden gemeinsam angewendet …")

    def discard_changes(self) -> None:
        if not self.discard_button.isEnabled():
            return
        self._pending_additions.clear()
        self._pending_deletions.clear()
        self._show_entries()
        self.status.setText("Vormerkungen verworfen.")
        self._update_controls()

    def start_rebuild(self) -> None:
        if not self.rebuild_button.isEnabled():
            return
        self._submit(
            "start", self._control.start_recognition_rebuild, "Neuaufbau wird angefordert …"
        )

    def cancel_rebuild(self) -> None:
        if not self.cancel_button.isEnabled() or self._job is None:
            return
        job_id = str(self._job["id"])
        control = self._control
        self._submit(
            "cancel",
            lambda: control.cancel_recognition_rebuild(job_id),
            "Abbruch wird angefordert …",
        )

    def poll_rebuild(self) -> None:
        if not self._active or self._control is None:
            return
        if not self._loaded:
            self.reload()
            return
        self._submit("poll", self._control.recognition_rebuild)

    def _submit(self, operation: str, function: Callable, message: str = "") -> None:
        if self._busy or self._closed:
            return
        self._busy = True
        self._operation = operation
        if message:
            self.status.setText(message)
        self._update_controls()

        def execute():
            try:
                return function()
            except ApiRejectedError as error:
                # Keep a busy or invalid-input response distinct from a lost
                # connection, so the user can still correct input or cancel.
                return error

        task = BackgroundTask(execute)
        task.signals.succeeded.connect(self._succeeded)
        task.signals.failed.connect(self._failed)
        task.signals.finished.connect(self._finished)
        self._tasks.add(task)
        self._pool.start(task)

    def _succeeded(self, result: object) -> None:
        if self._closed:
            return
        if isinstance(result, ApiRejectedError):
            details = result.payload.get("error")
            message = str(details.get("message") or result) if isinstance(details, Mapping) else str(result)
            if result.status in {401, 403} or result.status >= 500:
                self._failed(message)
            else:
                self.status.setText(f"Server hat die Anfrage abgelehnt: {message}")
            return
        was_online = self._online
        self._online = True
        if self._operation == "load":
            entries, job = result
            if isinstance(entries, Mapping):
                self._publication_pending = entries["publication_pending"]
                entries = entries["entries"]
            self._entries = list(entries)
            self._loaded = True
            self._show_entries()
            self._show_job(job)
            self.status.setText(f"Vom Server geladen · {len(self._entries)} Sperreinträge")
        elif self._operation == "apply":
            self._entries = list(result["entries"])
            self._pending_additions.clear()
            self._pending_deletions.clear()
            self._publication_pending = not result["published"]
            self._show_entries()
            self.status.setText(
                "Sperrliste gespeichert. Der aktualisierte Kundenstand konnte noch nicht "
                "veröffentlicht werden. Bitte „Veröffentlichung wiederholen“ wählen."
                if self._publication_pending else
                "Sperrlistenänderungen gemeinsam gespeichert und vorhandene Vorschläge aktualisiert."
            )
        else:
            if self._operation == "cancel" and isinstance(result, Mapping):
                self._cancel_requested_id = str(result["id"])
            self._show_job(result)
            if self._operation == "start":
                self.status.setText("Neuaufbau vom Server angenommen.")
            elif self._operation == "cancel":
                self.status.setText("Abbruch vom Server angenommen.")
            elif not was_online:
                self.status.setText("Verbindung zum Server wiederhergestellt.")

    def _failed(self, error: str) -> None:
        if self._closed:
            return
        self._online = False
        self.status.setText(f"Serveraktion fehlgeschlagen: {error} · Bitte aktualisieren.")
        self._update_controls()

    def _finished(self) -> None:
        sender = self.sender()
        self._tasks = {task for task in self._tasks if task.signals is not sender}
        self._busy = False
        self._operation = ""
        if not self._closed:
            self._update_controls()
            if self._active and self._reload_on_idle:
                self._reload_on_idle = False
                self.reload()

    def _show_entries(self) -> None:
        selected_ids = self._selected_ids()
        self.entry_list.setRowCount(0)
        labels = dict(BLOCKLIST_KINDS)
        for entry in [*self._entries, *self._pending_additions.values()]:
            entry_id = int(entry["id"])
            pending_delete = entry_id in self._pending_deletions
            row = self.entry_list.rowCount()
            self.entry_list.insertRow(row)
            for column, value in enumerate(
                (
                    labels.get(entry["kind"], entry["kind"]),
                    entry["value"],
                    entry.get("reason", ""),
                    entry.get("created_at", ""),
                    "Neue Sperre · unbestätigt" if entry_id < 0 else
                    "Entfernung · unbestätigt" if pending_delete else "Gespeichert",
                )
            ):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.ItemDataRole.UserRole, int(entry["id"]))
                item.setToolTip(str(value or ""))
                if entry_id < 0 or pending_delete:
                    font = item.font()
                    font.setItalic(True)
                    font.setStrikeOut(pending_delete)
                    item.setFont(font)
                self.entry_list.setItem(row, column, item)
            if entry_id in selected_ids:
                self.entry_list.selectionModel().select(
                    self.entry_list.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )

    def _selected_ids(self) -> set[int]:
        return {int(item.data(Qt.ItemDataRole.UserRole)) for item in self.entry_list.selectedItems()}

    def _selected_id(self) -> int | None:
        items = self.entry_list.selectedItems()
        return int(items[0].data(Qt.ItemDataRole.UserRole)) if items else None

    def _show_job(self, job: Mapping | None) -> None:
        self._job = dict(job) if job is not None else None
        if self._job is None:
            self.job_status.setText("Noch kein vollständiger Neuaufbau gestartet.")
            self.job_progress.hide()
            self._cancel_requested_id = None
            return
        state = str(self._job.get("state", ""))
        active = state in _ACTIVE_JOB_STATES
        if not active or str(self._job["id"]) != self._cancel_requested_id:
            self._cancel_requested_id = None
        label = _JOB_LABELS.get(state, f"Neuaufbau: {state}")
        if self._cancel_requested_id is not None:
            label = "Abbruch angefordert · laufende Verarbeitung wird beendet"
        details = []
        if self._job.get("created_at"):
            details.append(f"Gestartet: {self._job['created_at']}")
        if self._job.get("finished_at"):
            details.append(f"Beendet: {self._job['finished_at']}")
        if self._job.get("error"):
            details.append(f"Fehler: {self._job['error']}")
        workers = self._job.get("document_workers")
        if isinstance(workers, Mapping):
            details.extend(document_worker_summary(workers))
        self.job_status.setText("\n".join((label, *details)))
        self.job_progress.setRange(0, 0 if active else 100)
        self.job_progress.setVisible(active)

    def _update_controls(self) -> None:
        if not hasattr(self, "add_button"):
            return
        available = self._online and self._loaded and not self._busy and not self._closed
        editable = self._online and self._loaded and not self._closed and (
            not self._busy or self._operation == "poll"
        )
        self.kind_combo.setEnabled(editable)
        self.value_input.setEnabled(editable)
        self.reason_input.setEnabled(editable)
        self.add_button.setEnabled(available and bool(self.value_input.text().strip()))
        self.delete_button.setEnabled(available and self._selected_id() is not None)
        pending = len(self._pending_additions) + len(self._pending_deletions)
        selected = self._selected_ids()
        self.delete_button.setText("Entfernung zurücknehmen" if selected and
                                   selected <= self._pending_deletions else "Auswahl entfernen")
        self.apply_button.setEnabled(available and (pending > 0 or self._publication_pending))
        self.apply_button.setText(f"Änderungen bestätigen ({pending})" if pending else
                                 "Veröffentlichung wiederholen" if self._publication_pending else
                                 "Änderungen bestätigen")
        self.discard_button.setEnabled(pending > 0 and not self._busy and not self._closed)
        self.pending_status.setText(
            f"{len(self._pending_additions)} neue Sperren · {len(self._pending_deletions)} Entfernungen "
            "vorgemerkt. Noch nicht auf dem Server gespeichert."
            if pending else "Kundenstand noch nicht veröffentlicht."
            if self._publication_pending else "Keine unbestätigten Änderungen."
        )
        active = self._job is not None and self._job.get("state") in _ACTIVE_JOB_STATES
        self.rebuild_button.setEnabled(available and not active and pending == 0 and not self._publication_pending)
        self.rebuild_button.setToolTip("Bitte zuerst die Sperrlistenänderungen bestätigen oder verwerfen."
                                      if pending else "")
        self.cancel_button.setEnabled(
            available and active and self._cancel_requested_id is None
            and self._job.get("state") != "cancelling"
        )
        self.reload_button.setEnabled(not self._busy and not self._closed and self._control is not None)

    def set_active(self, active: bool) -> None:
        if self._closed or active == self._active:
            return
        self._active = active
        if not active:
            self._timer.stop()
            return
        self._timer.start()
        self._reload_on_idle = self._busy
        if not self._busy:
            self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.set_active(True)

    def hideEvent(self, event) -> None:
        self.set_active(False)
        super().hideEvent(event)

    def shutdown(self) -> None:
        self.set_active(False)
        self._closed = True
        self._timer.stop()
