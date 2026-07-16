from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.customer_models import Customer
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs import CustomerEditorDialog
from app.gui.widgets.buttons import AppButton


class CustomerOverviewPanel(QWidget):
    customerChanged = Signal()

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

        self.detail_summary = QTextEdit()
        self.detail_summary.setReadOnly(True)
        self.detail_summary.setPlaceholderText("Kundendetails werden hier angezeigt …")
        right_layout.addWidget(self.detail_summary, 1)

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
            self.detail_summary.clear()
            self.edit_button.setEnabled(False)
            return

        lines = [
            f"Typ: {customer.entity_type or '-'}",
            f"Firma: {customer.company or '-'}",
            f"E-Mail: {customer.email or '-'}",
            f"Telefon: {customer.phone or '-'}",
            f"Adresse: {', '.join(part for part in [customer.street, customer.postal_code, customer.city] if part) or '-'}",
            f"Dienstleistungen: {', '.join(customer.service_types) or '-'}",
            f"Verknüpfte Ordner: {', '.join(customer.folder_paths) or customer.folder_path or '-'}",
            f"Tags: {', '.join(customer.tags) or '-'}",
            f"Kontakte: {len(customer.contacts)}",
            f"Notizen: {len(customer.notes)}",
        ]
        if customer.contacts:
            lines.append("")
            lines.append("Kontaktdetails:")
            for contact in customer.contacts:
                values = [contact.name, contact.role, contact.email, contact.phone]
                lines.append("- " + " · ".join(value for value in values if value))
        if customer.notes:
            lines.append("")
            lines.append("Notizen:")
            for note in customer.notes:
                lines.append(f"- {note}")

        self.detail_title.setText(customer.display_name)
        self.detail_summary.setPlainText("\n".join(lines))
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
