from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.core.customer_models import Contact, Customer
from app.core.customer_repository import CustomerRepository
from app.gui.widgets.buttons import AppButton


class CustomerEditorDialog(QDialog):
    def __init__(
        self,
        repository: CustomerRepository,
        folder_path: str,
        suggested_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.customer = repository.get_by_folder(folder_path) or Customer(
            folder_path=folder_path,
            display_name=suggested_name,
            company=suggested_name,
        )
        self.setWindowTitle("Kundendaten bearbeiten")
        self.setMinimumSize(700, 560)

        layout = QVBoxLayout(self)
        title = QLabel(f"Verknüpfter Ordner: {folder_path}")
        title.setObjectName("PopupCaption")
        title.setWordWrap(True)
        layout.addWidget(title)
        tabs = QTabWidget()
        tabs.addTab(self._build_master_page(), "Stammdaten")
        tabs.addTab(self._build_contacts_page(), "Kontakte")
        tabs.addTab(self._build_notes_page(), "Notizen & Tags")
        layout.addWidget(tabs, 1)

        actions = QHBoxLayout()
        if self.customer.id is not None:
            delete_button = AppButton("Kundeneintrag löschen", AppButton.DANGER)
            delete_button.clicked.connect(self._delete_customer)
            actions.addWidget(delete_button)
        actions.addStretch()
        cancel_button = AppButton("Abbrechen", AppButton.SECONDARY)
        save_button = AppButton("Speichern")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._save)
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        layout.addLayout(actions)

    def _build_master_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.entity_type = QComboBox()
        self.entity_type.addItems(["Unternehmen", "Privatperson", "Organisation"])
        self.entity_type.setCurrentText(self.customer.entity_type)
        self.display_name = QLineEdit(self.customer.display_name)
        self.company = QLineEdit(self.customer.company)
        self.email = QLineEdit(self.customer.email)
        self.phone = QLineEdit(self.customer.phone)
        self.street = QLineEdit(self.customer.street)
        self.postal_code = QLineEdit(self.customer.postal_code)
        self.city = QLineEdit(self.customer.city)
        form.addRow("Art", self.entity_type)
        form.addRow("Anzeigename *", self.display_name)
        form.addRow("Firma/Organisation", self.company)
        form.addRow("E-Mail", self.email)
        form.addRow("Telefon", self.phone)
        form.addRow("Straße", self.street)
        form.addRow("PLZ", self.postal_code)
        form.addRow("Ort", self.city)
        return page

    def _build_contacts_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.contacts_table = QTableWidget(0, 4)
        self.contacts_table.setHorizontalHeaderLabels(["Name", "Funktion", "E-Mail", "Telefon"])
        self.contacts_table.horizontalHeader().setStretchLastSection(True)
        for contact in self.customer.contacts:
            self._append_contact(contact)
        layout.addWidget(self.contacts_table)
        actions = QHBoxLayout()
        add_button = AppButton("Kontakt hinzufügen", AppButton.SECONDARY)
        remove_button = AppButton("Ausgewählten entfernen", AppButton.DANGER)
        add_button.clicked.connect(lambda: self._append_contact(Contact()))
        remove_button.clicked.connect(self._remove_contact)
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def _append_contact(self, contact: Contact):
        row = self.contacts_table.rowCount()
        self.contacts_table.insertRow(row)
        for column, value in enumerate((contact.name, contact.role, contact.email, contact.phone)):
            self.contacts_table.setItem(row, column, QTableWidgetItem(value))

    def _remove_contact(self):
        row = self.contacts_table.currentRow()
        if row >= 0:
            self.contacts_table.removeRow(row)

    def _build_notes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Tags (durch Komma getrennt)"))
        self.tags_input = QLineEdit(", ".join(self.customer.tags))
        layout.addWidget(self.tags_input)
        layout.addWidget(QLabel("Notizen"))
        self.notes_list = QListWidget()
        self.notes_list.addItems(self.customer.notes)
        layout.addWidget(self.notes_list, 1)
        self.note_input = QPlainTextEdit()
        self.note_input.setPlaceholderText("Neue Notiz …")
        self.note_input.setMaximumHeight(100)
        layout.addWidget(self.note_input)
        actions = QHBoxLayout()
        add_button = AppButton("Notiz hinzufügen", AppButton.SECONDARY)
        remove_button = AppButton("Notiz entfernen", AppButton.DANGER)
        add_button.clicked.connect(self._add_note)
        remove_button.clicked.connect(self._remove_note)
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def _add_note(self):
        text = self.note_input.toPlainText().strip()
        if text:
            self.notes_list.addItem(text)
            self.note_input.clear()

    def _remove_note(self):
        row = self.notes_list.currentRow()
        if row >= 0:
            self.notes_list.takeItem(row)

    def _save(self):
        if not self.display_name.text().strip():
            QMessageBox.warning(self, "Kundendaten", "Bitte einen Anzeigenamen eingeben.")
            return
        contacts = []
        for row in range(self.contacts_table.rowCount()):
            values = [
                self.contacts_table.item(row, column).text().strip()
                if self.contacts_table.item(row, column) else ""
                for column in range(4)
            ]
            if values[0]:
                contacts.append(Contact(*values))
        self.customer.display_name = self.display_name.text().strip()
        self.customer.entity_type = self.entity_type.currentText()
        self.customer.company = self.company.text().strip()
        self.customer.email = self.email.text().strip()
        self.customer.phone = self.phone.text().strip()
        self.customer.street = self.street.text().strip()
        self.customer.postal_code = self.postal_code.text().strip()
        self.customer.city = self.city.text().strip()
        self.customer.contacts = contacts
        self.customer.notes = [self.notes_list.item(i).text() for i in range(self.notes_list.count())]
        self.customer.tags = [tag.strip() for tag in self.tags_input.text().split(",") if tag.strip()]
        self.repository.save(self.customer)
        self.accept()

    def _delete_customer(self):
        answer = QMessageBox.question(
            self,
            "Kundeneintrag löschen",
            "Nur die hinterlegten Kundendaten werden gelöscht. Dateien und Ordner bleiben erhalten.",
        )
        if answer == QMessageBox.Yes and self.customer.id is not None:
            self.repository.delete(self.customer.id)
            self.accept()
