"""Customer editor using the original 0.4.1 four-page visual design.

The dialog deliberately contains presentation logic only. Customer projects are
owned by the server and are therefore shown read-only instead of being discovered
or reassigned from the desktop client.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from papagui_contracts import Contact, Customer, CustomerProject

from .dialogs.centered_popup import CenteredPopupDialog
from .widgets.buttons import AppButton


class CustomerEditorDialog(CenteredPopupDialog):
    """Edit immutable customer master data without crossing client boundaries."""

    deleteRequested = Signal(object)
    CONTACT_HEADERS = ("Name", "Rolle", "Telefon", "E-Mail")

    def __init__(
        self,
        customer: Customer | None = None,
        *,
        explanation: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._original = customer or Customer()
        self._result: Customer | None = None
        self.setWindowTitle("Kunde bearbeiten" if customer else "Kunde anlegen")
        self.setMinimumSize(700, 560)
        self.resize(820, 640)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        body = QFrame()
        body.setObjectName("CustomerEditorBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        popup_title = QLabel(
            "Kundendaten bearbeiten" if customer is not None else "Kundendaten anlegen"
        )
        popup_title.setObjectName("PopupSectionTitle")
        layout.addWidget(popup_title)

        linked = QLabel(self._project_caption())
        linked.setObjectName("PopupCaption")
        linked.setWordWrap(True)
        linked.setToolTip(self._project_tooltip())
        layout.addWidget(linked)
        self.folder_summary = linked

        if explanation:
            explanation_label = QLabel(explanation)
            explanation_label.setObjectName("PopupWarning")
            explanation_label.setWordWrap(True)
            layout.addWidget(explanation_label)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("CustomerEditorTabs")
        self.tabs.addTab(self._build_master_page(), "Stammdaten")
        self.tabs.addTab(self._build_contacts_page(), "Kontakte")
        self.tabs.addTab(self._build_notes_page(), "Notiz")
        self.tabs.addTab(self._build_files_page(), "Dateien")
        layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        if self._original.id is not None:
            delete_button = AppButton("Kundeneintrag löschen", AppButton.DANGER)
            delete_button.setAccessibleName("Kundeneintrag löschen")
            delete_button.clicked.connect(self._request_delete)
            actions.addWidget(delete_button)
            self.delete_button = delete_button
        actions.addStretch(1)
        cancel_button = AppButton("Abbrechen", AppButton.SECONDARY)
        save_button = AppButton("Speichern")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._save)
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        self.cancel_button = cancel_button
        self.save_button = save_button
        layout.addLayout(actions)
        root_layout.addWidget(body)

        # Compatibility names retained for the existing 0.4.2 shell/tests.
        self.services = self.service_types
        self.notes = self.note_text
        self.contacts = self.contacts_table
        self.tags = QLineEdit(", ".join(self._original.tags), self)
        self.tags.hide()
        self.fields = {
            "display_name": self.display_name,
            "entity_type": self.entity_type,
            "company": self.company,
            "email": self.primary_email,
            "phone": self.primary_phone,
            "street": self.street,
            "postal_code": self.postal_code,
            "city": self.city,
        }

    def _build_master_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DialogPage")
        form = QFormLayout(page)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        self.entity_type = QComboBox()
        self.entity_type.setEditable(True)
        self.entity_type.addItems(("Unternehmen", "Privatperson", "Organisation"))
        self.entity_type.setCurrentText(self._original.entity_type)

        self.display_name = QLineEdit(self._original.display_name)
        self.display_name.setVisible(False)
        self.company = QLineEdit(self._original.company or self._original.display_name)
        self._initial_company_text = self.company.text()
        self.street = QLineEdit(self._original.street)
        self.postal_code = QLineEdit(self._original.postal_code)
        self.city = QLineEdit(self._original.city)
        self.service_types = QLineEdit(", ".join(self._original.service_types))
        self.primary_email = QLineEdit(self._original.email)
        self.primary_email.setVisible(False)
        self.primary_phone = QLineEdit(self._original.phone)
        self.primary_phone.setVisible(False)

        self.folder_paths = QLineEdit(self._project_summary())
        self.folder_paths.setReadOnly(True)
        self.folder_paths.setPlaceholderText("Serverseitig erkannte Projekte")
        self.folder_paths.setToolTip(self._project_tooltip())

        form.addRow("Art", self.entity_type)
        form.addRow("Unternehmensname *", self.company)
        form.addRow("Firmenadresse", self.street)
        form.addRow("PLZ", self.postal_code)
        form.addRow("Ort", self.city)
        form.addRow("Dienstleistungen", self.service_types)
        form.addRow("Verknüpfte Ordner", self.folder_paths)
        return page

    def _build_contacts_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DialogPage")
        layout = QVBoxLayout(page)

        self.contacts_table = QTableWidget(0, len(self.CONTACT_HEADERS))
        self.contacts_table.setObjectName("CustomerContactsTable")
        self.contacts_table.setHorizontalHeaderLabels(self.CONTACT_HEADERS)
        self.contacts_table.verticalHeader().setDefaultSectionSize(34)
        self.contacts_table.verticalHeader().setMinimumSectionSize(30)
        header = self.contacts_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for contact in self._original.contacts:
            self._append_contact(contact)
        self.entity_type.currentTextChanged.connect(
            self._update_contact_role_visibility
        )
        self._update_contact_role_visibility()
        layout.addWidget(self.contacts_table)

        actions = QHBoxLayout()
        add_button = AppButton("Kontakt hinzufügen", AppButton.SECONDARY)
        remove_button = AppButton("Ausgewählten entfernen", AppButton.DANGER)
        add_button.clicked.connect(lambda: self._append_contact(Contact()))
        remove_button.clicked.connect(self._remove_contact)
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _build_notes_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DialogPage")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Notiz"))
        self.note_text = QPlainTextEdit()
        self.note_text.setPlaceholderText("Freitextnotiz zum Kunden …")
        self.note_text.setPlainText("\n\n".join(self._original.notes))
        layout.addWidget(self.note_text, 1)
        return page

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DialogPage")
        layout = QVBoxLayout(page)

        hint = QLabel(
            "Projektordner werden vom Indexserver erkannt und verwaltet. Die "
            "bekannte Zuordnungsansicht bleibt erhalten; bis zu einem eigenen "
            "Server-API-Vertrag zeigt der Client die Einträge nur lesend an."
        )
        hint.setWordWrap(True)
        hint.setObjectName("StatCaption")
        layout.addWidget(hint)

        tables = QHBoxLayout()
        tables.setSpacing(10)

        selected_panel = QVBoxLayout()
        selected_panel.addWidget(QLabel("Ausgewählte Ordner"))
        self.selected_folders_table = self._create_project_table()
        self.selected_folders_table.setObjectName("SelectedCustomerFoldersTable")
        self.selected_folders_table.setToolTip(
            "Vom Indexserver bestätigte Projektzuordnungen (nur lesbar)"
        )
        self._fill_project_table(self.selected_folders_table, self._original.projects)
        selected_panel.addWidget(self.selected_folders_table, 1)

        found_panel = QVBoxLayout()
        found_panel.addWidget(QLabel("Gefundene mögliche Ordner"))
        self.found_folders_table = self._create_project_table()
        self.found_folders_table.setObjectName("SuggestedCustomerFoldersTable")
        self.found_folders_table.setToolTip(
            "Serverseitige Zuordnungsvorschläge; der notwendige API-Vertrag fehlt noch"
        )
        self.found_folders_table.setEnabled(False)
        found_panel.addWidget(self.found_folders_table, 1)
        found_hint = QLabel("Neue Zuordnungen erscheinen nach dem nächsten Serverlauf.")
        found_hint.setObjectName("PopupCaption")
        found_hint.setWordWrap(True)
        found_panel.addWidget(found_hint)

        tables.addLayout(selected_panel, 1)
        tables.addLayout(found_panel, 1)
        layout.addLayout(tables, 1)

        assignment_actions = QHBoxLayout()
        assignment_actions.setSpacing(8)
        self.remove_folder_button = AppButton(
            "Auswahl entfernen →", AppButton.SECONDARY
        )
        self.select_folder_button = AppButton(
            "← Ordner auswählen", AppButton.SECONDARY
        )
        unavailable_tooltip = (
            "Noch nicht verfügbar: Für manuelle Projektzuordnungen fehlt ein "
            "revisionierter Server-API-Vertrag."
        )
        for button in (self.remove_folder_button, self.select_folder_button):
            button.setEnabled(False)
            button.setToolTip(unavailable_tooltip)
        assignment_actions.addWidget(self.remove_folder_button)
        assignment_actions.addStretch(1)
        assignment_actions.addWidget(self.select_folder_button)
        layout.addLayout(assignment_actions)

        self.project_assignment_status = QLabel(
            "Zuordnungen können erst nach Ergänzung der Server-API geändert werden."
        )
        self.project_assignment_status.setObjectName("PopupCaption")
        self.project_assignment_status.setWordWrap(True)
        self.project_assignment_status.setToolTip(unavailable_tooltip)
        layout.addWidget(self.project_assignment_status)
        return page

    @staticmethod
    def _create_project_table() -> QTableWidget:
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(("Ordner", "Dienstleistung", "Jahr"))
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _fill_project_table(
        self, table: QTableWidget, projects: tuple[CustomerProject, ...]
    ) -> None:
        for project in projects:
            row = table.rowCount()
            table.insertRow(row)
            source = self._project_source(project)
            values = (
                project.project_label or source or "-",
                project.service_type or "-",
                str(project.year) if project.year is not None else "-",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(source)
                table.setItem(row, column, item)

    def _append_contact(self, contact: Contact) -> None:
        row = self.contacts_table.rowCount()
        self.contacts_table.insertRow(row)
        # The old table presents telephone before e-mail.
        for column, value in enumerate(
            (contact.name, contact.role, contact.phone, contact.email)
        ):
            self.contacts_table.setItem(row, column, QTableWidgetItem(value))

    def _update_contact_role_visibility(self, *_args) -> None:
        is_company = self.entity_type.currentText().strip() == "Unternehmen"
        self.contacts_table.setColumnHidden(1, not is_company)

    def _remove_contact(self) -> None:
        row = self.contacts_table.currentRow()
        if row >= 0:
            self.contacts_table.removeRow(row)

    def _read_contacts(self) -> tuple[Contact, ...]:
        contacts: list[Contact] = []
        for row in range(self.contacts_table.rowCount()):
            values = tuple(
                self.contacts_table.item(row, column).text().strip()
                if self.contacts_table.item(row, column) is not None
                else ""
                for column in range(len(self.CONTACT_HEADERS))
            )
            if any(values):
                contacts.append(
                    Contact(
                        name=values[0],
                        role=values[1],
                        phone=values[2],
                        email=values[3],
                    )
                )
        return tuple(contacts)

    def _build_customer(self) -> Customer:
        company_text = self.company.text().strip()
        # Preserve legacy records where only display_name was populated when the
        # user opens and saves without changing the visible fallback value.
        company_changed = company_text != self._initial_company_text
        company = company_text
        display_name = company_text or self.display_name.text().strip()
        if not company_changed and not self._original.company:
            company = ""
            display_name = self._original.display_name

        contacts = self._read_contacts()
        primary_email = next((item.email for item in contacts if item.email), "")
        primary_phone = next((item.phone for item in contacts if item.phone), "")
        if contacts == self._original.contacts:
            # Retain top-level data that may not have a corresponding contact.
            primary_email = self._original.email
            primary_phone = self._original.phone

        notes_text = self.note_text.toPlainText().strip()
        notes = (notes_text,) if notes_text else ()
        services = tuple(
            value.strip()
            for value in self.service_types.text().split(",")
            if value.strip()
        )
        tags = tuple(
            value.strip() for value in self.tags.text().split(",") if value.strip()
        )
        return replace(
            self._original,
            display_name=display_name,
            entity_type=self.entity_type.currentText().strip() or "Unternehmen",
            service_types=services,
            company=company,
            email=primary_email,
            phone=primary_phone,
            street=self.street.text().strip(),
            postal_code=self.postal_code.text().strip(),
            city=self.city.text().strip(),
            contacts=contacts,
            notes=notes,
            tags=tags,
        )

    def customer(self) -> Customer:
        """Return current immutable form data without accepting the dialog."""

        return self._result or self._build_customer()

    def _save(self) -> None:
        customer = self._build_customer()
        if not customer.display_name.strip():
            QMessageBox.warning(
                self,
                "Kundendaten",
                "Bitte einen Unternehmens- oder Anzeigenamen eingeben.",
            )
            return
        self._result = customer
        self.accept()

    def _request_delete(self) -> None:
        answer = QMessageBox.question(
            self,
            "Kundeneintrag löschen",
            "Nur die hinterlegten Kundendaten werden gelöscht. Dateien und Ordner "
            "bleiben erhalten.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.deleteRequested.emit(self._original)
            self.accept()

    def _project_summary(self) -> str:
        if self._original.projects:
            return " | ".join(
                project.project_label or self._project_source(project)
                for project in self._original.projects
            )
        values = self._original.folder_paths or (
            (self._original.folder_path,) if self._original.folder_path else ()
        )
        return " | ".join(values)

    def _project_caption(self) -> str:
        summary = self._project_summary()
        return f"Verknüpfter Ordner: {summary or '-'}"

    def _project_tooltip(self) -> str:
        if self._original.projects:
            return "\n".join(
                self._project_source(project) for project in self._original.projects
            )
        values = self._original.folder_paths or (
            (self._original.folder_path,) if self._original.folder_path else ()
        )
        return "\n".join(values)

    @staticmethod
    def _project_source(project: CustomerProject) -> str:
        if project.source is None:
            return ""
        return f"{project.source.source_id}:{project.source.relative_path}"
