"""Read-oriented customer details with focused journal and review actions."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from papagui_contracts import Customer, CustomerJournalEntry


class JournalEditorDialog(QDialog):
    def __init__(self, entry: CustomerJournalEntry | None = None, parent=None):
        super().__init__(parent)
        self._original = entry or CustomerJournalEntry()
        self.setWindowTitle("Journaleintrag bearbeiten" if entry else "Journaleintrag anlegen")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

        self.title = QLineEdit(self._original.title)
        self.body = QPlainTextEdit(self._original.body)
        form.addRow("Titel", self.title)
        form.addRow("Text", self.body)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def entry(self) -> CustomerJournalEntry:
        from dataclasses import replace

        return replace(
            self._original,
            title=self.title.text().strip(),
            body=self.body.toPlainText().strip(),
        )


class CustomerDetailWidget(QWidget):
    journalAddRequested = Signal()
    journalEditRequested = Signal(object)
    journalDeleteRequested = Signal(object)
    suggestionDecisionRequested = Signal(object, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._customer: Customer | None = None
        self._journal_views = ()
        self._suggestions = ()
        self._suggestion_revision = 0
        layout = QVBoxLayout(self)
        self.heading = QLabel("Kein Kunde ausgewählt")
        self.heading.setStyleSheet("font-size: 18px; font-weight: 700")
        layout.addWidget(self.heading)
        self.address = QLabel()
        self.address.setWordWrap(True)
        layout.addWidget(self.address)
        self.tabs = QTabWidget()
        self.projects = QListWidget()
        self.contacts = QListWidget()
        self.notes = QListWidget()
        self.tags = QListWidget()
        self.journal = QListWidget()
        self.suggestions = QListWidget()
        for widget, title in (
            (self.projects, "Projekte"),
            (self.contacts, "Kontakte"),
            (self.notes, "Notizen"),
            (self.tags, "Tags"),
            (self.journal, "Journal"),
            (self.suggestions, "Dokumentvorschläge"),
        ):
            self.tabs.addTab(widget, title)
        layout.addWidget(self.tabs, 1)
        journal_actions = QHBoxLayout()
        for label, callback in (
            ("Journaleintrag neu", self.journalAddRequested.emit),
            ("Bearbeiten", self._emit_edit),
            ("Löschen", self._emit_delete),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            journal_actions.addWidget(button)
        journal_actions.addStretch()
        layout.addLayout(journal_actions)
        suggestion_actions = QHBoxLayout()
        for label, action in (
            ("Vorschlag annehmen", "accept"),
            ("Vorschlag ablehnen", "reject"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, value=action: self._emit_suggestion(value)
            )
            suggestion_actions.addWidget(button)
        suggestion_actions.addStretch()
        layout.addLayout(suggestion_actions)

    @property
    def customer(self) -> Customer | None:
        return self._customer

    def set_customer(self, customer: Customer | None) -> None:
        self._customer = customer
        for widget in (self.projects, self.contacts, self.notes, self.tags):
            widget.clear()
        if customer is None:
            self.heading.setText("Kein Kunde ausgewählt")
            self.address.clear()
            self.set_journal(())
            self.set_suggestions((), 0)
            return
        self.heading.setText(customer.display_name or f"Kunde {customer.id}")
        self.address.setText(
            " · ".join(
                value
                for value in (
                    customer.company,
                    " ".join((customer.street, customer.postal_code, customer.city)).strip(),
                    customer.email,
                    customer.phone,
                )
                if value
            )
        )
        for project in customer.projects:
            source = (
                f"{project.source.source_id}:{project.source.relative_path}"
                if project.source is not None
                else "keine Quelle"
            )
            self.projects.addItem(
                " · ".join(
                    value
                    for value in (
                        project.project_label or source,
                        project.service_type,
                        str(project.year or ""),
                        project.project_city,
                        f"Quelle: {source} ({project.provenance})",
                    )
                    if value
                )
            )
        for contact in customer.contacts:
            self.contacts.addItem(
                " · ".join(
                    value
                    for value in (contact.name, contact.role, contact.email, contact.phone)
                    if value
                )
            )
        self.notes.addItems(list(customer.notes))
        self.tags.addItems(list(customer.tags))

    def set_journal(self, views) -> None:
        self._journal_views = tuple(views)
        self.journal.clear()
        for view in self._journal_views:
            entry = view.entry
            badge = "" if view.sync_state.value == "synced" else f" [{view.sync_state.value}]"
            self.journal.addItem(
                f"{entry.entry_number or '–'} · {entry.title or '(ohne Titel)'}{badge}\n{entry.body}"
            )

    def set_suggestions(self, suggestions, revision: int) -> None:
        self._suggestions = tuple(suggestions)
        self._suggestion_revision = revision
        self.suggestions.clear()
        for suggestion in self._suggestions:
            source = suggestion.source
            self.suggestions.addItem(
                f"{suggestion.field_name}: {suggestion.value} "
                f"({suggestion.confidence:.0%})\n"
                f"{source.source_id}:{source.relative_path} · {suggestion.rule}\n"
                f"{suggestion.excerpt}"
            )

    def selected_journal_view(self):
        row = self.journal.currentRow()
        return self._journal_views[row] if 0 <= row < len(self._journal_views) else None

    def selected_suggestion(self):
        row = self.suggestions.currentRow()
        return self._suggestions[row] if 0 <= row < len(self._suggestions) else None

    def _emit_edit(self) -> None:
        if view := self.selected_journal_view():
            self.journalEditRequested.emit(view)

    def _emit_delete(self) -> None:
        if view := self.selected_journal_view():
            self.journalDeleteRequested.emit(view)

    def _emit_suggestion(self, action: str) -> None:
        if suggestion := self.selected_suggestion():
            self.suggestionDecisionRequested.emit(
                suggestion, action, self._suggestion_revision
            )
