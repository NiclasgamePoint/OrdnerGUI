from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.customer_models import Customer, CustomerJournalEntry, CustomerProject
from app.core.config import DB_FILE, load_customer_recognition_options
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs import CustomerDataSuggestionsDialog, CustomerEditorDialog
from app.gui.widgets.buttons import AppButton, CountBadgeButton
from app.gui.widgets.result_row import ResultRow


class _JournalEntryEditorDialog(QDialog):
    def __init__(
        self,
        title: str,
        body: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Journal-Eintrag bearbeiten")
        self.setModal(True)
        self.resize(560, 340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.title_input = QLineEdit(title)
        self.title_input.setPlaceholderText("Titel (optional)")
        layout.addWidget(self.title_input)

        self.body_input = QPlainTextEdit()
        self.body_input.setPlaceholderText("Eintrag …")
        self.body_input.setPlainText(body)
        layout.addWidget(self.body_input, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return self.title_input.text().strip(), self.body_input.toPlainText().strip()


class _JournalEntryCard(QWidget):
    def __init__(self, entry: CustomerJournalEntry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("JournalEntryCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        number_label = QLabel(f"#{entry.entry_number}")
        number_label.setObjectName("SectionTitle")
        top_row.addWidget(number_label)
        top_row.addStretch(1)

        timestamp = QLabel(CustomerPage._format_journal_date(entry.created_at))
        faded = QColor(self.palette().text().color())
        faded.setAlpha(160)
        timestamp.setStyleSheet(f"color: {faded.name(QColor.NameFormat.HexArgb)};")
        top_row.addWidget(timestamp)
        layout.addLayout(top_row)

        if entry.title.strip():
            title_label = QLabel(entry.title)
            title_label.setObjectName("PopupSectionTitle")
            layout.addWidget(title_label)

        body_label = QLabel(entry.body)
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body_label)


class CustomerPage(QWidget):
    """Dedicated customer details page with linked project folders."""

    backRequested = Signal()
    customerChanged = Signal(int)
    folderActivated = Signal(str)
    openPathRequested = Signal(str)

    def __init__(
        self,
        repository: CustomerRepository,
        parent=None,
        index_path: Path = DB_FILE,
    ):
        super().__init__(parent)
        self.setObjectName("CustomerPage")
        self.repository = repository
        self.index_path = index_path
        self.customer: Customer | None = None
        self._notes_sync_in_progress = False
        self._journal_entries_by_id: dict[int, CustomerJournalEntry] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("PageSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_customer_card())
        self.splitter.addWidget(self._build_folder_card())
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([440, 660])
        layout.addWidget(self.splitter)

    def _build_customer_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        back_button = AppButton("←", AppButton.SECONDARY, minimum_width=48)
        back_button.setObjectName("BackButton")
        back_button.setToolTip("Zur vorherigen Seite")
        back_button.clicked.connect(self.backRequested.emit)
        self.customer_title = QLabel("Kundendaten")
        self.customer_title.setObjectName("PageTitle")
        header.addWidget(back_button)
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
        self.services = self._value_label()
        self.tags = self._value_label()
        form.addRow("Art", self.entity_type)
        form.addRow("Unternehmen", self.company)
        form.addRow("E-Mail", self.email)
        form.addRow("Telefon", self.phone)
        form.addRow("Adresse", self.address)
        form.addRow("Dienstleistungen", self.services)
        form.addRow("Tags", self.tags)
        form_layout.addLayout(form)

        contacts_title = QLabel("Kontakte")
        contacts_title.setObjectName("SectionTitle")
        form_layout.addWidget(contacts_title)
        self.contacts_table = QTableWidget(0, 4)
        self.contacts_table.setHorizontalHeaderLabels(
            ["Name", "Rolle", "E-Mail", "Telefon"]
        )
        self.contacts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.contacts_table.setMouseTracking(True)
        self.contacts_table.horizontalHeader().setStretchLastSection(True)
        self.contacts_table.setMinimumHeight(150)
        self.contacts_table.setAccessibleName("Kontakte des Kunden")
        self.contacts_table.setAccessibleDescription(
            "Tabelle mit Name, Rolle, E-Mail und Telefonnummer."
        )
        form_layout.addWidget(self.contacts_table)

        self.customer_tabs = QTabWidget()
        self.customer_tabs.addTab(self._build_notes_tab(), "Notizen")
        self.customer_tabs.addTab(self._build_journal_tab(), "Journal")
        form_layout.addWidget(self.customer_tabs)
        form_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        button_row = QHBoxLayout()
        self.edit_button = AppButton("Kundendaten bearbeiten", AppButton.SECONDARY)
        self.edit_button.setAccessibleName("Kundendaten bearbeiten")
        self.edit_button.clicked.connect(self._edit_customer)
        self.review_button = CountBadgeButton(
            "Kundendaten prüfen", AppButton.SECONDARY
        )
        self.review_button.setAccessibleName("Kundendaten prüfen")
        self.review_button.clicked.connect(self._review_suggestions)
        button_row.addWidget(self.edit_button, 1)
        button_row.addWidget(self.review_button, 1)
        layout.addLayout(button_row)
        return card

    def _build_notes_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(False)
        self.notes.setMinimumHeight(120)
        self.notes.setPlaceholderText("Notizen zum Kunden …")
        self.notes.setAccessibleName("Kundennotizen")
        layout.addWidget(self.notes, 1)

        actions = QHBoxLayout()
        self.notes_status = QLabel("")
        self.notes_status.setObjectName("PopupCaption")
        actions.addWidget(self.notes_status)
        actions.addStretch(1)
        self.save_notes_button = AppButton("Notizen speichern", AppButton.SECONDARY)
        self.save_notes_button.clicked.connect(self._save_notes)
        actions.addWidget(self.save_notes_button)
        layout.addLayout(actions)
        return page

    def _build_journal_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.journal_title_input = QLineEdit()
        self.journal_title_input.setPlaceholderText("Titel (optional)")
        self.journal_title_input.setAccessibleName("Titel für Journal-Eintrag")
        layout.addWidget(self.journal_title_input)

        self.journal_input = QPlainTextEdit()
        self.journal_input.setPlaceholderText("Neuen Journal-Eintrag schreiben …")
        self.journal_input.setMinimumHeight(90)
        self.journal_input.setAccessibleName("Neuer Journal-Eintrag")
        layout.addWidget(self.journal_input)

        actions = QHBoxLayout()
        self.journal_status = QLabel("")
        self.journal_status.setObjectName("PopupCaption")
        actions.addWidget(self.journal_status)
        actions.addStretch(1)
        self.add_journal_button = AppButton("Journal-Eintrag speichern", AppButton.SECONDARY)
        self.add_journal_button.clicked.connect(self._add_journal_entry)
        actions.addWidget(self.add_journal_button)
        layout.addLayout(actions)

        self.journal_list = QListWidget()
        self.journal_list.setObjectName("JournalEntryList")
        self.journal_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.journal_list.customContextMenuRequested.connect(self._open_journal_context_menu)
        self.journal_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.journal_list.setMinimumHeight(190)
        self.journal_list.setAccessibleName("Journal des Kunden")
        layout.addWidget(self.journal_list, 1)
        return page

    def _build_folder_card(self) -> QWidget:
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
        self._folder_rows: list[ResultRow] = []
        return card

    def _value_label(self) -> QLabel:
        label = QLabel("-")
        label.setObjectName("CustomerValue")
        label.setWordWrap(True)
        return label

    def set_customer(
        self,
        customer: Customer,
        folder_summaries: dict[str, dict] | None = None,
    ):
        self.customer = customer
        self.customer_title.setText(customer.display_name)
        self.entity_type.setText(customer.entity_type or "-")
        self.company.setText(customer.company or customer.display_name or "-")
        self.email.setText(customer.email or "-")
        self.phone.setText(customer.phone or "-")
        address = ", ".join(
            value
            for value in (
                customer.street,
                " ".join(
                    value
                    for value in (customer.postal_code, customer.city)
                    if value
                ),
            )
            if value
        )
        self.address.setText(address or "-")
        self.services.setText(", ".join(customer.service_types) or "-")
        self.tags.setText(", ".join(customer.tags) or "-")

        self.contacts_table.setRowCount(0)
        for contact in customer.contacts:
            row = self.contacts_table.rowCount()
            self.contacts_table.insertRow(row)
            self.contacts_table.setItem(row, 0, QTableWidgetItem(contact.name))
            self.contacts_table.setItem(row, 1, QTableWidgetItem(contact.role))
            self.contacts_table.setItem(row, 2, QTableWidgetItem(contact.email))
            self.contacts_table.setItem(row, 3, QTableWidgetItem(contact.phone))
        self._notes_sync_in_progress = True
        self.notes.setPlainText("\n\n".join(customer.notes))
        self.notes_status.setText("")
        self.journal_status.setText("")
        self._notes_sync_in_progress = False
        self._reload_journal_entries()
        self._refresh_suggestion_count()
        self._set_projects(customer, folder_summaries or {})

    def _save_notes(self):
        if self.customer is None or self.customer.id is None or self._notes_sync_in_progress:
            return
        text = self.notes.toPlainText().strip()
        self.customer.notes = [text] if text else []
        updated = self.repository.save(self.customer)
        self.customer = updated
        self.notes_status.setText("Notizen gespeichert")
        self.customerChanged.emit(int(updated.id))

    def _add_journal_entry(self):
        if self.customer is None or self.customer.id is None:
            return
        entry = self.repository.add_journal_entry(
            int(self.customer.id),
            self.journal_input.toPlainText(),
            self.journal_title_input.text(),
        )
        if entry is None:
            self.journal_status.setText("Bitte zuerst einen Text eingeben")
            return
        self.journal_title_input.clear()
        self.journal_input.clear()
        self.journal_status.setText("Journal-Eintrag gespeichert")
        self._reload_journal_entries()

    def _reload_journal_entries(self):
        self.journal_list.clear()
        self._journal_entries_by_id.clear()
        if self.customer is None or self.customer.id is None:
            return
        entries = self.repository.list_journal_entries(int(self.customer.id))
        for entry in entries:
            self._journal_entries_by_id[int(entry.id)] = entry
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, int(entry.id))
            card = _JournalEntryCard(entry, self.journal_list)
            item.setSizeHint(card.sizeHint())
            self.journal_list.addItem(item)
            self.journal_list.setItemWidget(item, card)

    def _open_journal_context_menu(self, position):
        item = self.journal_list.itemAt(position)
        if item is None:
            return
        entry_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        entry = self._journal_entries_by_id.get(entry_id)
        if entry is None:
            return

        menu = QMenu(self)
        edit_action = menu.addAction("Bearbeiten")
        delete_action = menu.addAction("Löschen")
        selected = menu.exec(self.journal_list.viewport().mapToGlobal(position))
        if selected == edit_action:
            self._edit_journal_entry(entry)
        elif selected == delete_action:
            self._delete_journal_entry(entry)

    def _edit_journal_entry(self, entry: CustomerJournalEntry):
        if self.customer is None or self.customer.id is None or entry.id is None:
            return
        dialog = _JournalEntryEditorDialog(entry.title, entry.body, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, body = dialog.values()
        updated = self.repository.update_journal_entry(
            int(self.customer.id),
            int(entry.id),
            body,
            title,
        )
        if updated is None:
            self.journal_status.setText("Eintrag konnte nicht gespeichert werden")
            return
        self.journal_status.setText("Journal-Eintrag aktualisiert")
        self._reload_journal_entries()

    def _delete_journal_entry(self, entry: CustomerJournalEntry):
        if self.customer is None or self.customer.id is None or entry.id is None:
            return
        answer = QMessageBox.question(
            self,
            "Journal-Eintrag löschen",
            "Soll dieser Journal-Eintrag wirklich gelöscht werden?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = self.repository.delete_journal_entry(
            int(self.customer.id),
            int(entry.id),
        )
        if not deleted:
            self.journal_status.setText("Eintrag konnte nicht gelöscht werden")
            return
        self.journal_status.setText("Journal-Eintrag gelöscht")
        self._reload_journal_entries()

    @staticmethod
    def _format_journal_date(value: str) -> str:
        if not value:
            return "-"
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return value

    def _set_projects(self, customer: Customer, summaries: dict[str, dict]):
        for row in self._folder_rows:
            self.folder_layout.removeWidget(row)
            row.deleteLater()
        self._folder_rows.clear()

        projects = self._projects_for_customer(customer)
        self.folder_message.setVisible(not projects)
        for project in projects:
            path = project.folder_path
            summary = summaries.get(path, {})
            details = [project.service_type or "Dienstleistung"]
            if project.year is not None:
                details.append(str(project.year))
            if project.project_label:
                details.append(project.project_label)
            details.append(f"{int(summary.get('file_count') or 0)} Dateien")
            modified = str(summary.get("last_modified") or "")
            if modified:
                try:
                    modified = datetime.fromisoformat(modified).strftime("%d.%m.%Y")
                except ValueError:
                    pass
                details.append(f"geändert {modified}")
            details.append(path)
            title = " · ".join(
                value
                for value in (
                    project.service_type or "Dienstleistung",
                    str(project.year) if project.year is not None else "",
                    project.project_city or "Ort unbekannt",
                )
                if value
            )
            row = ResultRow(
                title,
                " · ".join(details),
                path,
                path,
            )
            row.activated.connect(lambda value: self.folderActivated.emit(str(value)))
            row.openPathRequested.connect(self.openPathRequested.emit)
            self.folder_layout.insertWidget(self.folder_layout.count() - 1, row)
            self._folder_rows.append(row)

    def _projects_for_customer(self, customer: Customer) -> list[CustomerProject]:
        if customer.id is not None:
            projects = self.repository.list_projects_for_customer(int(customer.id))
            if projects:
                return projects
        folders = customer.folder_paths or (
            [customer.folder_path] if customer.folder_path else []
        )
        fallback_projects = []
        for index, folder in enumerate(folders):
            service = (
                customer.service_types[min(index, len(customer.service_types) - 1)]
                if customer.service_types
                else ""
            )
            fallback_projects.append(self._project_from_folder(folder, service))
        return sorted(
            fallback_projects,
            key=lambda project: (
                project.year is None,
                -(project.year or 0),
                project.service_type.casefold(),
                project.project_label.casefold(),
            ),
        )

    def _project_from_folder(self, folder: str, service_type: str) -> CustomerProject:
        path = Path(folder)
        year = None
        service = service_type.strip()
        project_label = path.name if folder else ""
        parts = path.parts
        for index, part in enumerate(parts):
            if part.isdigit() and len(part) == 4:
                year = int(part)
                if not service and index > 0:
                    service = parts[index - 1]
                if index + 1 < len(parts):
                    project_label = parts[index + 1]
                break
        return CustomerProject(
            service_type=service or "Dienstleistung",
            folder_path=folder,
            project_label=project_label,
            year=year,
        )

    def _edit_customer(self):
        if self.customer is None or self.customer.id is None:
            return
        dialog = CustomerEditorDialog(
            self.repository,
            customer_id=self.customer.id,
            parent=self,
        )
        if dialog.exec():
            updated = self.repository.get(self.customer.id)
            if updated is not None:
                self.set_customer(updated)
                self.customerChanged.emit(updated.id)

    def _review_suggestions(self):
        if self.customer is None or self.customer.id is None:
            return
        dialog = CustomerDataSuggestionsDialog(
            self.repository,
            int(self.customer.id),
            self.index_path,
            load_customer_recognition_options(),
            parent=self,
        )
        dialog.suggestionsChanged.connect(self.review_button.set_count)
        dialog.customerChanged.connect(self._reload_customer)
        dialog.exec()
        self._reload_customer(int(self.customer.id))

    def _reload_customer(self, customer_id: int):
        updated = self.repository.get(customer_id)
        if updated is not None:
            self.set_customer(updated)
            self.customerChanged.emit(customer_id)

    def _refresh_suggestion_count(self):
        count = 0
        if self.customer is not None and self.customer.id is not None:
            count = len(self.repository.list_data_suggestions(int(self.customer.id)))
        self.review_button.set_count(count)
        self.review_button.setToolTip(
            f"{count} Kontaktdaten zu prüfen"
            if count
            else "Keine Kontaktdaten zu prüfen"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = Qt.Vertical if self.width() < 900 else Qt.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
