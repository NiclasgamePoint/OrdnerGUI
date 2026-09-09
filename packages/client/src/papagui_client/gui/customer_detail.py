"""Customer presentation in the complete 0.4.1 two-card layout."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from papagui_contracts import Customer, CustomerJournalEntry, CustomerProject

from .customer_suggestions import (
    CustomerSuggestionsDialog as _CustomerSuggestionsDialog,
    quality_label,
    recognition_text,
)

from .dialogs.centered_popup import CenteredPopupDialog
from .widgets.buttons import AppButton, CountBadgeButton
from .widgets.result_row import ResultRow


class _ItemStore:
    """Tiny QListWidget-compatible state facade kept for the 0.4.2 public API."""

    def __init__(self) -> None:
        self._items: list[SimpleNamespace] = []
        self._row = -1

    def clear(self) -> None:
        self._items.clear()
        self._row = -1

    def addItem(self, text: str) -> None:  # noqa: N802 - Qt compatibility
        self._items.append(SimpleNamespace(text=lambda value=str(text): value))

    def addItems(self, values) -> None:  # noqa: N802 - Qt compatibility
        for value in values:
            self.addItem(str(value))

    def count(self) -> int:
        return len(self._items)

    def item(self, row: int):
        return self._items[row]

    def currentRow(self) -> int:  # noqa: N802 - Qt compatibility
        return self._row

    def setCurrentRow(self, row: int) -> None:  # noqa: N802 - Qt compatibility
        self._row = row


class _ContactTable(QTableWidget):
    """Old visible contact table with the former one-argument item convenience."""

    def item(self, row: int, column: int | None = None):  # type: ignore[override]
        if column is not None:
            return super().item(row, column)
        values = []
        for current_column in range(self.columnCount()):
            cell = super().item(row, current_column)
            if cell is not None and cell.text():
                values.append(cell.text())
        return SimpleNamespace(text=lambda: " · ".join(values))


class _NotesEdit(QPlainTextEdit):
    def item(self, row: int):
        values = self._logical_notes()
        return SimpleNamespace(text=lambda: values[row])

    def count(self) -> int:
        return len(self._logical_notes())

    def _logical_notes(self) -> tuple[str, ...]:
        return tuple(
            value.strip()
            for value in self.toPlainText().split("\n\n")
            if value.strip()
        )


class _TagLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__("-", parent)
        self._values: tuple[str, ...] = ()

    def set_values(self, values) -> None:
        self._values = tuple(values)
        self.setText(", ".join(self._values) or "-")

    def count(self) -> int:
        return len(self._values)

    def item(self, row: int):
        return SimpleNamespace(text=lambda: self._values[row])


class JournalEditorDialog(CenteredPopupDialog):
    """Original compact journal popup backed by the immutable journal contract."""

    def __init__(self, entry: CustomerJournalEntry | None = None, parent=None):
        super().__init__(parent)
        self._original = entry or CustomerJournalEntry()
        self.setWindowTitle(
            "Journaleintrag bearbeiten" if entry else "Journaleintrag anlegen"
        )
        self.resize(560, 340)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("CustomerEditorBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel(self.windowTitle())
        heading.setObjectName("PopupSectionTitle")
        layout.addWidget(heading)
        self.title = QLineEdit(self._original.title)
        self.title.setPlaceholderText("Titel (optional)")
        self.body = QPlainTextEdit(self._original.body)
        self.body.setPlaceholderText("Eintrag …")
        # v0.4.1 compatibility names.
        self.title_input = self.title
        self.body_input = self.body
        layout.addWidget(self.title)
        layout.addWidget(self.body, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        root.addWidget(body)

    def entry(self) -> CustomerJournalEntry:
        return replace(
            self._original,
            title=self.title.text().strip(),
            body=self.body.toPlainText().strip(),
        )

    def values(self) -> tuple[str, str]:
        value = self.entry()
        return value.title, value.body


class _JournalEntryCard(QFrame):
    editRequested = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.entry = view.entry
        self.setObjectName("JournalEntryCard")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._open_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        top_row = QHBoxLayout()
        number = self.entry.entry_number or self.entry.id or "–"
        number_label = QLabel(f"#{number}")
        number_label.setObjectName("SectionTitle")
        top_row.addWidget(number_label)
        sync_state = getattr(getattr(view, "sync_state", None), "value", "synced")
        if sync_state != "synced":
            badge = QLabel(
                {
                    "pending": "Lokal vorgemerkt",
                    "conflict": "Konflikt",
                    "awaiting_snapshot": "Übertragen",
                }.get(sync_state, sync_state)
            )
            badge.setObjectName("PopupWarning" if sync_state == "conflict" else "PopupCaption")
            top_row.addWidget(badge)
        top_row.addStretch(1)
        timestamp = QLabel(CustomerDetailWidget.format_journal_date(self.entry.created_at))
        faded = QColor(self.palette().text().color())
        faded.setAlpha(160)
        timestamp.setStyleSheet(f"color: {faded.name(QColor.NameFormat.HexArgb)};")
        top_row.addWidget(timestamp)
        layout.addLayout(top_row)
        if self.entry.title.strip():
            title = QLabel(self.entry.title)
            title.setObjectName("PopupSectionTitle")
            layout.addWidget(title)
        body = QLabel(self.entry.body)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)

    def _open_context_menu(self, position) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction("Bearbeiten")
        delete_action = menu.addAction("Löschen")
        selected = menu.exec(self.mapToGlobal(position))
        if selected is edit_action:
            self.editRequested.emit(self.view)
        elif selected is delete_action:
            self.deleteRequested.emit(self.view)


class CustomerDetailWidget(QWidget):
    """Customer details with the original master-data and services cards."""

    backRequested = Signal()
    customerChanged = Signal(int)
    folderActivated = Signal(str)
    openPathRequested = Signal(str)
    customerEditRequested = Signal()
    customerSaveRequested = Signal(object)
    journalAddRequested = Signal()
    journalCreateRequested = Signal(object)
    journalEditRequested = Signal(object)
    journalDeleteRequested = Signal(object)
    suggestionDecisionRequested = Signal(object, str, int)
    suggestionPageRequested = Signal(int)
    suggestionSourceRequested = Signal(object)
    recognitionRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CustomerPage")
        self._customer: Customer | None = None
        self._journal_views = ()
        self._suggestions = ()
        self._suggestion_revision = 0
        self._suggestion_total = 0
        self._suggestion_offset = 0
        self._suggestion_has_more = False
        self._recognition_status = {}
        self._suggestion_error = ""
        self._suggestion_offline = False
        self._suggestion_busy = False
        self._suggestion_dialog = None
        self._notes_sync_in_progress = False
        self._folder_rows: list[ResultRow] = []
        self._journal_cards: list[_JournalEntryCard] = []

        # Public collection facades from the initial split UI remain supported.
        self.projects = _ItemStore()
        self.journal = _ItemStore()
        self.suggestions = _ItemStore()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("PageSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_customer_card())
        self.splitter.addWidget(self._build_services_card())
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes((440, 660))
        layout.addWidget(self.splitter)

        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(450)
        self._notes_timer.timeout.connect(self._emit_notes_change)

    @property
    def customer(self) -> Customer | None:
        return self._customer

    def _build_customer_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.back_button = AppButton("←", AppButton.SECONDARY, minimum_width=48)
        self.back_button.setObjectName("BackButton")
        self.back_button.setToolTip("Zur vorherigen Seite")
        self.back_button.clicked.connect(self.backRequested.emit)
        self.customer_title = QLabel("Kundendaten")
        self.customer_title.setObjectName("PageTitle")
        self.heading = self.customer_title
        header.addWidget(self.back_button)
        header.addWidget(self.customer_title)
        header.addStretch(1)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setObjectName("PageScrollArea")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("ThemedScrollContent")
        form_layout = QVBoxLayout(content)
        form_layout.setContentsMargins(2, 2, 6, 2)
        form_layout.setSpacing(12)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        self.entity_type = self._value_label()
        self.company = self._value_label()
        self.email = self._value_label()
        self.phone = self._value_label()
        self.address = self._value_label()
        self.tags = _TagLabel()
        self.tags.setObjectName("CustomerValue")
        self.tags.setWordWrap(True)
        form.addRow("Art", self.entity_type)
        form.addRow("Unternehmen", self.company)
        form.addRow("E-Mail", self.email)
        form.addRow("Telefon", self.phone)
        form.addRow("Adresse", self.address)
        form.addRow("Tags", self.tags)
        form_layout.addLayout(form)

        contacts_title = QLabel("Kontakte")
        contacts_title.setObjectName("SectionTitle")
        form_layout.addWidget(contacts_title)
        self.contacts = _ContactTable(0, 4)
        self.contacts.setHorizontalHeaderLabels(("Name", "Rolle", "Telefon", "E-Mail"))
        self.contacts.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.contacts.setMouseTracking(True)
        contact_header = self.contacts.horizontalHeader()
        contact_header.setStretchLastSection(False)
        contact_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        contact_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.contacts.setMinimumHeight(150)
        self.contacts.setAccessibleName("Kontakte des Kunden")
        form_layout.addWidget(self.contacts)

        self.customer_tabs = QTabWidget()
        self.customer_tabs.setObjectName("InsetContentTabs")
        self.customer_tabs.addTab(self._build_notes_tab(), "Notizen")
        self.customer_tabs.addTab(self._build_journal_tab(), "Journal")
        self.customer_tabs.currentChanged.connect(
            self._update_journal_entries_visibility
        )
        form_layout.addWidget(self.customer_tabs)

        self.journal_entries_section = QWidget()
        self.journal_entries_section.setObjectName("JournalEntriesSection")
        entries_layout = QVBoxLayout(self.journal_entries_section)
        entries_layout.setContentsMargins(0, 0, 0, 0)
        entries_layout.setSpacing(8)
        title = QLabel("Bisherige Einträge")
        title.setObjectName("SectionTitle")
        entries_layout.addWidget(title)
        self.journal_entries_container = QWidget()
        self.journal_entries_layout = QVBoxLayout(self.journal_entries_container)
        self.journal_entries_layout.setContentsMargins(0, 0, 0, 0)
        self.journal_entries_layout.setSpacing(10)
        self.journal_entries_layout.addStretch(1)
        entries_layout.addWidget(self.journal_entries_container)
        self.journal_entries_section.setVisible(False)
        form_layout.addWidget(self.journal_entries_section)
        form_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self.edit_button = AppButton("Kundendaten bearbeiten", AppButton.SECONDARY)
        self.edit_button.clicked.connect(self.customerEditRequested.emit)
        self.review_button = CountBadgeButton("Kundendaten prüfen", AppButton.SECONDARY)
        self.review_button.clicked.connect(self._open_suggestions)
        buttons.addWidget(self.edit_button, 1)
        buttons.addWidget(self.review_button, 1)
        layout.addLayout(buttons)
        self.recognition_status_label = QLabel("")
        self.recognition_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.recognition_status_label.setWordWrap(True)
        self.recognition_status_label.setObjectName("PopupCaption")
        layout.addWidget(self.recognition_status_label)
        return card

    def _build_notes_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.notes = _NotesEdit()
        self.notes.setFixedHeight(196)
        self.notes.setPlaceholderText("Notizen zum Kunden …")
        self.notes.setAccessibleName("Kundennotizen")
        self.notes.textChanged.connect(self._notes_changed)
        layout.addWidget(self.notes, 1)
        actions = QHBoxLayout()
        self.notes_status = QLabel("")
        self.notes_status.setObjectName("PopupCaption")
        self._apply_subtle_hint_style(self.notes_status)
        actions.addWidget(self.notes_status)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _build_journal_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.journal_title_input = QLineEdit()
        self.journal_title_input.setPlaceholderText("Titel (optional)")
        self.journal_input = QPlainTextEdit()
        self.journal_input.setPlaceholderText("Neuen Journal-Eintrag schreiben …")
        self.journal_input.setFixedHeight(112)
        layout.addWidget(self.journal_title_input)
        layout.addWidget(self.journal_input)
        layout.addSpacing(8)
        actions = QHBoxLayout()
        self.journal_status = QLabel("")
        self.journal_status.setObjectName("PopupCaption")
        actions.addWidget(self.journal_status)
        actions.addStretch(1)
        self.add_journal_button = AppButton(
            "Journal-Eintrag speichern", AppButton.SECONDARY
        )
        self.add_journal_button.clicked.connect(self._create_journal_entry)
        actions.addWidget(self.add_journal_button)
        layout.addLayout(actions)
        return page

    def _build_services_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        title = QLabel("Dienstleistungen")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        self.folder_scroll = QScrollArea()
        self.folder_scroll.setObjectName("PageScrollArea")
        self.folder_scroll.setWidgetResizable(True)
        self.folder_scroll.setAccessibleName("Dienstleistungen und Projektordner")
        content = QWidget()
        content.setObjectName("ThemedScrollContent")
        self.folder_layout = QVBoxLayout(content)
        self.folder_layout.setContentsMargins(2, 2, 6, 2)
        self.folder_layout.setSpacing(7)
        self.folder_message = QLabel("Keine Dienstleistungen verknüpft")
        self.folder_message.setObjectName("SearchSectionMessage")
        self.folder_layout.addWidget(self.folder_message)
        self.folder_layout.addStretch(1)
        self.folder_scroll.setWidget(content)
        layout.addWidget(self.folder_scroll, 1)
        return card

    @staticmethod
    def _value_label() -> QLabel:
        label = QLabel("-")
        label.setObjectName("CustomerValue")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _apply_subtle_hint_style(label: QLabel) -> None:
        font = QFont(label.font())
        font.setPointSize(max(font.pointSize() - 1, 9))
        label.setFont(font)
        faded = QColor(label.palette().text().color())
        faded.setAlpha(115)
        label.setStyleSheet(f"color: {faded.name(QColor.NameFormat.HexArgb)};")

    def set_customer(
        self,
        customer: Customer | None,
        folder_summaries: dict[str, dict] | None = None,
    ) -> None:
        self._customer = customer
        self.projects.clear()
        self.contacts.setRowCount(0)
        self.tags.set_values(())
        if customer is None:
            self.heading.setText("Kein Kunde ausgewählt")
            for label in (
                self.entity_type,
                self.company,
                self.email,
                self.phone,
                self.address,
            ):
                label.setText("-")
            self._notes_sync_in_progress = True
            self.notes.clear()
            self._notes_sync_in_progress = False
            self.set_journal(())
            self.set_suggestions((), 0)
            self._set_projects((), {})
            self.edit_button.setEnabled(False)
            return

        self.edit_button.setEnabled(True)
        self.heading.setText(customer.display_name or f"Kunde {customer.id}")
        self.entity_type.setText(customer.entity_type or "-")
        self.company.setText(customer.company or customer.display_name or "-")
        self.email.setText(customer.email or "-")
        self.phone.setText(customer.phone or "-")
        address = ", ".join(
            value
            for value in (
                customer.street,
                " ".join(
                    value for value in (customer.postal_code, customer.city) if value
                ),
            )
            if value
        )
        self.address.setText(address or "-")
        self.tags.set_values(customer.tags)
        self._update_contacts(customer)
        self._notes_sync_in_progress = True
        self.notes.setPlainText("\n\n".join(customer.notes))
        self.notes_status.clear()
        self._notes_sync_in_progress = False
        self._set_projects(customer.projects, folder_summaries or {})

    def _update_contacts(self, customer: Customer) -> None:
        self.contacts.setRowCount(0)
        show_role = (
            customer.entity_type.strip() == "Unternehmen"
            and any(item.role.strip() for item in customer.contacts)
        )
        self.contacts.setColumnHidden(1, not show_role)
        for contact in customer.contacts:
            row = self.contacts.rowCount()
            self.contacts.insertRow(row)
            for column, value in enumerate(
                (contact.name, contact.role, contact.phone, contact.email)
            ):
                QTableWidget.setItem(
                    self.contacts, row, column, QTableWidgetItem(value)
                )

    def _set_projects(
        self,
        projects: tuple[CustomerProject, ...],
        summaries: dict[str, dict],
    ) -> None:
        for row in self._folder_rows:
            self.folder_layout.removeWidget(row)
            row.deleteLater()
        self._folder_rows.clear()
        self.projects.clear()
        self.folder_message.setVisible(not projects)
        for project in projects:
            source = self._project_source(project)
            summary = summaries.get(source, {})
            local_path = str(summary.get("local_path") or "")
            details = [project.service_type or "Dienstleistung"]
            if project.year is not None:
                details.append(str(project.year))
            if project.project_label:
                details.append(project.project_label)
            if "file_count" in summary:
                details.append(f"{int(summary.get('file_count') or 0)} Dateien")
            modified = str(summary.get("last_modified") or "")
            if modified:
                try:
                    modified = datetime.fromisoformat(modified).strftime("%d.%m.%Y")
                except ValueError:
                    pass
                details.append(f"geändert {modified}")
            details.append(f"Quelle: {source or 'keine Quelle'} ({project.provenance})")
            title = " · ".join(
                value
                for value in (
                    project.service_type or "Dienstleistung",
                    str(project.year) if project.year is not None else "",
                    project.project_city or "Ort unbekannt",
                )
                if value
            )
            row = ResultRow(title, " · ".join(details), source, local_path)
            row.activated.connect(
                lambda value: self.folderActivated.emit(str(value))
            )
            row.openPathRequested.connect(self.openPathRequested.emit)
            self.folder_layout.insertWidget(self.folder_layout.count() - 1, row)
            self._folder_rows.append(row)
            self.projects.addItem(
                " · ".join(
                    value
                    for value in (
                        project.project_label or source,
                        project.service_type,
                        str(project.year or ""),
                        project.project_city,
                        f"Quelle: {source or 'keine Quelle'} ({project.provenance})",
                    )
                    if value
                )
            )

    def set_project_summaries(self, summaries: dict[str, dict]) -> None:
        """Refresh project metadata without resetting editable customer fields."""

        if self._customer is not None:
            self._set_projects(self._customer.projects, summaries)

    @staticmethod
    def _project_source(project: CustomerProject) -> str:
        if project.source is None:
            return ""
        return f"{project.source.source_id}:{project.source.relative_path}"

    def set_journal(self, views) -> None:
        self._journal_views = tuple(views)
        self.journal.clear()
        while self.journal_entries_layout.count() > 1:
            item = self.journal_entries_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._journal_cards.clear()
        for view in reversed(self._journal_views):
            entry = view.entry
            sync_state = getattr(getattr(view, "sync_state", None), "value", "synced")
            badge = "" if sync_state == "synced" else f" [{sync_state}]"
            self.journal.addItem(
                f"{entry.entry_number or '–'} · {entry.title or '(ohne Titel)'}{badge}\n{entry.body}"
            )
            card = _JournalEntryCard(view, self.journal_entries_container)
            card.editRequested.connect(self.journalEditRequested.emit)
            card.deleteRequested.connect(self.journalDeleteRequested.emit)
            self.journal_entries_layout.insertWidget(
                self.journal_entries_layout.count() - 1, card
            )
            self._journal_cards.append(card)

    def set_suggestions(self, suggestions, revision: int, *, total=None, offset=0,
                        has_more=False, recognition=None, offline=False) -> None:
        self._suggestions = tuple(suggestions)
        self._suggestion_revision = revision
        self._suggestion_total = len(self._suggestions) if total is None else total
        self._suggestion_offset = offset
        self._suggestion_has_more = has_more
        self._recognition_status = dict(recognition or {})
        self._suggestion_offline = offline
        self._suggestion_error = ""
        self.suggestions.clear()
        for suggestion in self._suggestions:
            self.suggestions.addItem(
                f"{suggestion.field_name}: {suggestion.value} "
                f"({quality_label(suggestion)})\n"
                f"{suggestion.source.source_id}:{suggestion.source.relative_path} · "
                f"{suggestion.rule}\n{suggestion.excerpt}"
            )
        count = self._suggestion_total
        self.review_button.setEnabled(True)
        self.review_button.set_count(count)
        self.review_button.setToolTip(
            f"{count} Kontaktdaten zu prüfen"
            if count
            else "Keine Kontaktdaten zu prüfen"
        )
        self.recognition_status_label.setText(
            ("Offline – gespeicherter Datenstand. " if offline else "")
            + recognition_text(self._recognition_status)
        )
        self._update_suggestion_dialog()

    def set_suggestions_loading(self, revision: int) -> None:
        """Expose a non-blocking loading state while the server is queried."""

        self.set_suggestions((), revision)
        self.review_button.setEnabled(False)
        self.review_button.setToolTip("Kundendaten werden geladen …")
        self.recognition_status_label.setText("Kundendaten werden geladen …")

    def set_suggestions_error(self, error: str, revision: int) -> None:
        """Keep the detail page usable when the optional server query fails."""

        self._suggestion_error = error
        self._suggestion_revision = revision
        self.review_button.setEnabled(True)
        self.review_button.setToolTip(
            f"Kundendaten konnten nicht geladen werden: {error}"
        )
        self.recognition_status_label.setText(f"Kundendaten konnten nicht geladen werden: {error}")
        self._update_suggestion_dialog()

    def _update_suggestion_dialog(self) -> None:
        dialog = self._suggestion_dialog
        if dialog is not None and hasattr(dialog, "set_data"):
            dialog.set_data(
                self._suggestions, self._suggestion_revision, self._customer,
                total=self._suggestion_total, offset=self._suggestion_offset,
                has_more=self._suggestion_has_more, recognition=self._recognition_status,
                offline=self._suggestion_offline, error=self._suggestion_error,
            )
            dialog.set_busy(self._suggestion_busy)

    def set_suggestions_busy(self, busy: bool) -> None:
        self._suggestion_busy = busy
        if self._suggestion_dialog is not None:
            self._suggestion_dialog.set_busy(busy)

    def suggestion_rejection_reason(self, suggestion_id: int) -> str:
        if self._suggestion_dialog is None:
            return ""
        return self._suggestion_dialog._rejection_reasons.get(suggestion_id, "")

    def apply_suggestion_decision(self, suggestion_id: int, customer: Customer) -> None:
        """Keep a live dialog and the next write on the returned server revision."""
        self.set_customer(customer)
        self._suggestions = tuple(item for item in self._suggestions if item.id != suggestion_id)
        self._suggestion_revision = customer.revision
        self._suggestion_total = max(0, self._suggestion_total - 1)
        self.set_suggestions(
            self._suggestions, customer.revision, total=self._suggestion_total,
            offset=self._suggestion_offset, has_more=self._suggestion_has_more,
            recognition=self._recognition_status,
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

    def _open_suggestions(self) -> None:
        dialog = _CustomerSuggestionsDialog(
            self._suggestions,
            self._suggestion_revision,
            self,
            self._customer,
        )
        self._suggestion_dialog = dialog
        dialog.decisionRequested.connect(self.suggestionDecisionRequested.emit)
        if hasattr(dialog, "pageRequested"):
            dialog.pageRequested.connect(self.suggestionPageRequested.emit)
            dialog.sourceRequested.connect(self.suggestionSourceRequested.emit)
            dialog.recognitionRequested.connect(self.recognitionRequested.emit)
            dialog.manualResolutionRequested.connect(self.customerEditRequested.emit)
            self._update_suggestion_dialog()
        try:
            dialog.exec()
        finally:
            self._suggestion_dialog = None

    def _create_journal_entry(self) -> None:
        body = self.journal_input.toPlainText().strip()
        if not body:
            self.journal_status.setText("Bitte zuerst einen Text eingeben")
            return
        entry = CustomerJournalEntry(
            customer_id=self._customer.id if self._customer is not None else None,
            title=self.journal_title_input.text().strip(),
            body=body,
        )
        self.journalCreateRequested.emit(entry)
        self.journal_title_input.clear()
        self.journal_input.clear()
        self.journal_status.setText("Journal-Eintrag lokal vorgemerkt")

    def _notes_changed(self) -> None:
        if not self._notes_sync_in_progress and self._customer is not None:
            self.notes_status.setText("Änderung wird gespeichert …")
            self._notes_timer.start()

    def _emit_notes_change(self) -> None:
        if self._customer is None:
            return
        text = self.notes.toPlainText().strip()
        notes = (text,) if text else ()
        if notes == self._customer.notes:
            self.notes_status.clear()
            return
        updated = replace(self._customer, notes=notes)
        self._customer = updated
        self.customerSaveRequested.emit(updated)
        self.notes_status.setText("Lokal gespeichert · Synchronisierung ausstehend")

    def _update_journal_entries_visibility(self, current_index: int) -> None:
        self.journal_entries_section.setVisible(current_index == 1)

    @staticmethod
    def format_journal_date(value: str) -> str:
        if not value:
            return "-"
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return value

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        orientation = (
            Qt.Orientation.Vertical
            if self.width() < 900
            else Qt.Orientation.Horizontal
        )
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)


# Stable names for the pre-split UI and for the first separated-client shell.
CustomerPage = CustomerDetailWidget
CustomerDataSuggestionsDialog = _CustomerSuggestionsDialog
_JournalEntryEditorDialog = JournalEditorDialog
