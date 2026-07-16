from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.customer_models import Customer
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs import CustomerEditorDialog
from app.gui.widgets.buttons import AppButton


class CustomerOverviewPanel(QWidget):
    customerChanged = Signal()
    folderSearchRequested = Signal(str)

    def __init__(self, repository: CustomerRepository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.customers: list[Customer] = []
        self.selected_customer_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Kunden suchen …")
        self.search_input.textChanged.connect(self._reload_customers)
        left_layout.addWidget(self.search_input)

        self.customer_list = QListWidget()
        self.customer_list.itemSelectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.customer_list, 1)

        list_actions = QHBoxLayout()
        self.refresh_button = AppButton("Aktualisieren", AppButton.SECONDARY)
        self.refresh_button.clicked.connect(self._reload_customers)
        list_actions.addWidget(self.refresh_button)
        list_actions.addStretch(1)
        left_layout.addLayout(list_actions)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.detail_title = QLabel("Kein Kunde ausgewählt")
        self.detail_title.setObjectName("StatValue")
        right_layout.addWidget(self.detail_title)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self._build_master_page(), "Stammdaten")
        self.detail_tabs.addTab(self._build_contacts_page(), "Kontakte")
        self.detail_tabs.addTab(self._build_notes_page(), "Notiz")
        self.detail_tabs.addTab(self._build_files_page(), "Dateien")
        right_layout.addWidget(self.detail_tabs, 1)

        detail_actions = QHBoxLayout()
        self.edit_button = AppButton("Kunde bearbeiten", AppButton.SECONDARY)
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self._edit_selected)
        detail_actions.addWidget(self.edit_button)
        detail_actions.addStretch(1)
        right_layout.addLayout(detail_actions)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([320, 640])
        layout.addWidget(splitter, 1)

        self._reload_customers()

    def _readonly_line_edit(self) -> QLineEdit:
        field = QLineEdit()
        field.setReadOnly(True)
        return field

    def _build_master_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.detail_entity_type = self._readonly_line_edit()
        self.detail_company = self._readonly_line_edit()
        self.detail_street = self._readonly_line_edit()
        self.detail_postal_code = self._readonly_line_edit()
        self.detail_city = self._readonly_line_edit()
        self.detail_service_types = self._readonly_line_edit()
        self.detail_folder_paths = self._readonly_line_edit()

        form.addRow("Art", self.detail_entity_type)
        form.addRow("Unternehmensname", self.detail_company)
        form.addRow("Firmenadresse", self.detail_street)
        form.addRow("PLZ", self.detail_postal_code)
        form.addRow("Ort", self.detail_city)
        form.addRow("Dienstleistungen", self.detail_service_types)
        form.addRow("Verknüpfte Ordner", self.detail_folder_paths)
        return page

    def _build_contacts_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.detail_contacts_table = QTableWidget(0, 3)
        self.detail_contacts_table.setHorizontalHeaderLabels(["Name", "E-Mail", "Telefon"])
        self.detail_contacts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.detail_contacts_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.detail_contacts_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.detail_contacts_table)
        return page

    def _build_notes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.detail_note_text = QPlainTextEdit()
        self.detail_note_text.setReadOnly(True)
        self.detail_note_text.setPlaceholderText("Keine Notiz hinterlegt")
        layout.addWidget(self.detail_note_text, 1)
        return page

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.detail_files_tree = QTreeWidget()
        self.detail_files_tree.setHeaderLabels(["Ordner / Datei", "Typ", "Größe"])
        self.detail_files_tree.itemDoubleClicked.connect(self._on_file_tree_item_double_clicked)
        header = self.detail_files_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.detail_files_tree, 1)
        return page

    def _on_file_tree_item_double_clicked(self, item: QTreeWidgetItem, _column: int):
        if item is None:
            return
        if item.text(1) != "Ordner":
            return
        label = item.text(0).strip()
        if not label or label == "-" or label.startswith("("):
            return
        query = Path(label).name or label
        if query:
            self.folderSearchRequested.emit(query)

    def _set_readonly_text(self, field: QLineEdit, value: str):
        field.setText(value or "-")

    def _populate_files_tree(self, customer: Customer):
        self.detail_files_tree.clear()
        folders = customer.folder_paths or ([customer.folder_path] if customer.folder_path else [])
        if not folders:
            self.detail_files_tree.addTopLevelItem(QTreeWidgetItem(["-", "Ordner", "-"]))
            return

        for folder in folders:
            root_item = QTreeWidgetItem([folder, "Ordner", ""])
            self.detail_files_tree.addTopLevelItem(root_item)
            folder_path = Path(folder)
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
            root_item.setExpanded(False)

    def refresh(self):
        self._reload_customers()

    def _reload_customers(self):
        query = self.search_input.text().strip()
        if query:
            self.customers = self.repository.search(query, limit=500)
        else:
            self.customers = self.repository.list_customers()

        previous_id = self.selected_customer_id
        self.customer_list.blockSignals(True)
        self.customer_list.clear()
        for customer in self.customers:
            label = customer.display_name
            if customer.entity_type:
                label = f"{label} · {customer.entity_type}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, customer.id)
            item.setToolTip(customer.folder_path or "")
            self.customer_list.addItem(item)
        self.customer_list.blockSignals(False)

        if previous_id is not None:
            for row in range(self.customer_list.count()):
                item = self.customer_list.item(row)
                if item.data(Qt.UserRole) == previous_id:
                    self.customer_list.setCurrentRow(row)
                    break
        if self.customer_list.currentRow() < 0 and self.customer_list.count() > 0:
            self.customer_list.setCurrentRow(0)
        if self.customer_list.count() == 0:
            self.selected_customer_id = None
            self._set_detail(None)

    def _on_selection_changed(self):
        item = self.customer_list.currentItem()
        if item is None:
            self.selected_customer_id = None
            self._set_detail(None)
            return
        customer_id = item.data(Qt.UserRole)
        self.selected_customer_id = int(customer_id) if customer_id is not None else None
        customer = self.repository.get(self.selected_customer_id) if self.selected_customer_id else None
        self._set_detail(customer)

    def _set_detail(self, customer: Customer | None):
        if customer is None:
            self.detail_title.setText("Kein Kunde ausgewählt")
            self._set_readonly_text(self.detail_entity_type, "")
            self._set_readonly_text(self.detail_company, "")
            self._set_readonly_text(self.detail_street, "")
            self._set_readonly_text(self.detail_postal_code, "")
            self._set_readonly_text(self.detail_city, "")
            self._set_readonly_text(self.detail_service_types, "")
            self._set_readonly_text(self.detail_folder_paths, "")
            self.detail_contacts_table.setRowCount(0)
            self.detail_note_text.clear()
            self.detail_files_tree.clear()
            self.edit_button.setEnabled(False)
            return

        self.detail_title.setText(customer.display_name)
        self._set_readonly_text(self.detail_entity_type, customer.entity_type)
        self._set_readonly_text(self.detail_company, customer.company)
        self._set_readonly_text(self.detail_street, customer.street)
        self._set_readonly_text(self.detail_postal_code, customer.postal_code)
        self._set_readonly_text(self.detail_city, customer.city)
        self._set_readonly_text(self.detail_service_types, ", ".join(customer.service_types))
        self._set_readonly_text(
            self.detail_folder_paths,
            " | ".join(customer.folder_paths) or customer.folder_path,
        )

        self.detail_contacts_table.setRowCount(0)
        for contact in customer.contacts:
            row = self.detail_contacts_table.rowCount()
            self.detail_contacts_table.insertRow(row)
            self.detail_contacts_table.setItem(row, 0, QTableWidgetItem(contact.name))
            self.detail_contacts_table.setItem(row, 1, QTableWidgetItem(contact.email))
            self.detail_contacts_table.setItem(row, 2, QTableWidgetItem(contact.phone))

        self.detail_note_text.setPlainText("\n\n".join(customer.notes))
        self._populate_files_tree(customer)
        self.edit_button.setEnabled(True)

    def _edit_selected(self):
        if self.selected_customer_id is None:
            return
        dialog = CustomerEditorDialog(
            self.repository,
            customer_id=self.selected_customer_id,
            parent=self,
        )
        if dialog.exec():
            self._reload_customers()
            self.customerChanged.emit()
