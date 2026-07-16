from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QHeaderView,
    QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.core.customer_models import Contact, Customer
from app.core.customer_repository import CustomerRepository
from app.gui.widgets.buttons import AppButton
from app.services.customer_suggestion import CustomerSuggestionService


class CustomerEditorDialog(QDialog):
    AUTO_FILL_STYLE = "border: 1px solid #d7a832; background-color: rgba(215, 168, 50, 0.12);"

    def __init__(
        self,
        repository: CustomerRepository,
        folder_path: str = "",
        suggested_name: str = "",
        customer_id: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.context_folder_path = folder_path
        self._suggestion_service = CustomerSuggestionService()
        self.customer = (
            repository.get(customer_id)
            if customer_id is not None
            else repository.get_by_folder(folder_path)
        ) or Customer(
            folder_path=folder_path,
            folder_paths=[folder_path] if folder_path else [],
            display_name=suggested_name,
            company=suggested_name,
        )
        self.setWindowTitle("Kundendaten bearbeiten")
        self.setMinimumSize(700, 560)

        layout = QVBoxLayout(self)
        linked_folder = folder_path or (self.customer.folder_paths[0] if self.customer.folder_paths else "-")
        title = QLabel(f"Verknüpfter Ordner: {linked_folder}")
        title.setObjectName("PopupCaption")
        title.setWordWrap(True)
        layout.addWidget(title)
        tabs = QTabWidget()
        tabs.addTab(self._build_master_page(), "Stammdaten")
        tabs.addTab(self._build_contacts_page(), "Kontakte")
        tabs.addTab(self._build_notes_page(), "Notiz")
        tabs.addTab(self._build_files_page(), "Dateien")
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

        if self.customer.id is None and self.context_folder_path:
            self._offer_auto_suggestions(suggested_name)

    def _build_master_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.entity_type = QComboBox()
        self.entity_type.setEditable(True)
        self.entity_type.addItems(self.repository.list_customer_types())
        if self.entity_type.count() == 0:
            self.entity_type.addItems(["Unternehmen", "Privatperson", "Organisation"])
        self.entity_type.setCurrentText(self.customer.entity_type)
        self.display_name = QLineEdit(self.customer.display_name)
        self.display_name.setVisible(False)
        self.company = QLineEdit(self.customer.company)
        self.street = QLineEdit(self.customer.street)
        self.postal_code = QLineEdit(self.customer.postal_code)
        self.city = QLineEdit(self.customer.city)
        self.service_types = QLineEdit(", ".join(self.customer.service_types))
        folder_values = self.customer.folder_paths or ([self.customer.folder_path] if self.customer.folder_path else [])
        self.folder_paths = QLineEdit(", ".join(folder_values))
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
        layout = QVBoxLayout(page)
        self.contacts_table = QTableWidget(0, 3)
        self.contacts_table.setHorizontalHeaderLabels(["Name", "E-Mail", "Telefon"])
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

    def _append_contact(self, contact: Contact, auto_filled: bool = False):
        row = self.contacts_table.rowCount()
        self.contacts_table.insertRow(row)
        for column, value in enumerate((contact.name, contact.email, contact.phone)):
            item = QTableWidgetItem(value)
            if auto_filled:
                item.setBackground(self.palette().alternateBase())
                item.setToolTip("Automatisch aus Dokument-/Ordnerdaten vorausgefüllt")
            self.contacts_table.setItem(row, column, item)

    def _remove_contact(self):
        row = self.contacts_table.currentRow()
        if row >= 0:
            self.contacts_table.removeRow(row)

    def _build_notes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Notiz"))
        self.note_text = QPlainTextEdit()
        self.note_text.setPlaceholderText("Freitextnotiz zum Kunden …")
        self.note_text.setPlainText("\n\n".join(self.customer.notes))
        layout.addWidget(self.note_text, 1)
        return page

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.files_tree = QTreeWidget()
        self.files_tree.setHeaderLabels(["Ordner / Datei", "Typ", "Größe"])
        header = self.files_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._populate_files_tree()
        layout.addWidget(self.files_tree, 1)
        return page

    def _mark_auto_filled_widget(self, widget: QWidget):
        widget.setStyleSheet(self.AUTO_FILL_STYLE)
        widget.setToolTip("Automatisch aus Dokument-/Ordnerdaten vorausgefüllt")

    def _populate_files_tree(self):
        self.files_tree.clear()
        folders = [
            value.strip()
            for value in self.folder_paths.text().split(",")
            if value.strip()
        ] or ([self.context_folder_path] if self.context_folder_path else [])

        for folder in folders:
            folder_path = Path(folder)
            root_item = QTreeWidgetItem([str(folder_path), "Ordner", ""])
            self.files_tree.addTopLevelItem(root_item)
            if not folder_path.exists() or not folder_path.is_dir():
                root_item.addChild(QTreeWidgetItem(["(nicht gefunden)", "-", "-"]))
                continue

            file_count = 0
            for file_path in sorted(folder_path.rglob("*")):
                if not file_path.is_file():
                    continue
                relative_name = str(file_path.relative_to(folder_path))
                extension = file_path.suffix.lower().lstrip(".") or "-"
                size_kb = f"{file_path.stat().st_size / 1024:.1f} KB"
                root_item.addChild(QTreeWidgetItem([relative_name, extension, size_kb]))
                file_count += 1
                if file_count >= 500:
                    root_item.addChild(QTreeWidgetItem(["… weitere Dateien ausgelassen", "", ""]))
                    break
            root_item.setExpanded(True)

    def _offer_auto_suggestions(self, suggested_name: str):
        suggestion = self._suggestion_service.suggest_for_folder(
            Path(self.context_folder_path),
            suggested_name=suggested_name,
        )
        if not suggestion.has_values():
            return

        if suggestion.display_name:
            self.display_name.setText(suggestion.display_name)
        if suggestion.company:
            self.company.setText(suggestion.company)
            self._mark_auto_filled_widget(self.company)
        if suggestion.entity_type:
            self.entity_type.setCurrentText(suggestion.entity_type)
            self._mark_auto_filled_widget(self.entity_type)
        if suggestion.street:
            self.street.setText(suggestion.street)
            self._mark_auto_filled_widget(self.street)
        if suggestion.postal_code:
            self.postal_code.setText(suggestion.postal_code)
            self._mark_auto_filled_widget(self.postal_code)
        if suggestion.city:
            self.city.setText(suggestion.city)
            self._mark_auto_filled_widget(self.city)

        merged_services = [
            item.strip()
            for item in self.service_types.text().split(",")
            if item.strip()
        ]
        for item in suggestion.service_types:
            if item not in merged_services:
                merged_services.append(item)
        self.service_types.setText(", ".join(merged_services))
        if suggestion.service_types:
            self._mark_auto_filled_widget(self.service_types)

        # Suggestion contacts are appended as dedicated contacts.
        for contact in suggestion.contacts:
            if not contact.name.strip():
                continue
            self._append_contact(contact, auto_filled=True)

    def _save(self):
        if not self.company.text().strip():
            QMessageBox.warning(self, "Kundendaten", "Bitte einen Unternehmensnamen eingeben.")
            return
        contacts = []
        for row in range(self.contacts_table.rowCount()):
            values = [
                self.contacts_table.item(row, column).text().strip()
                if self.contacts_table.item(row, column) else ""
                for column in range(3)
            ]
            if values[0]:
                contacts.append(Contact(values[0], "", values[1], values[2]))
        company_name = self.company.text().strip()
        self.customer.display_name = company_name
        self.customer.entity_type = self.entity_type.currentText().strip() or "Unternehmen"
        self.customer.service_types = [
            value.strip() for value in self.service_types.text().split(",") if value.strip()
        ]
        folders = [value.strip() for value in self.folder_paths.text().split(",") if value.strip()]
        if self.context_folder_path and self.context_folder_path not in folders:
            folders.insert(0, self.context_folder_path)
        self.customer.folder_paths = folders
        if folders:
            self.customer.folder_path = folders[0]
        self.customer.company = company_name
        primary_email = next((contact.email for contact in contacts if contact.email.strip()), "")
        primary_phone = next((contact.phone for contact in contacts if contact.phone.strip()), "")
        self.customer.email = primary_email
        self.customer.phone = primary_phone
        self.customer.street = self.street.text().strip()
        self.customer.postal_code = self.postal_code.text().strip()
        self.customer.city = self.city.text().strip()
        self.customer.contacts = contacts
        note_text = self.note_text.toPlainText().strip()
        self.customer.notes = [note_text] if note_text else []
        self.customer.tags = []
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
