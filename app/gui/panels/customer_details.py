from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from app.core.customer_repository import CustomerRepository
from app.gui.dialogs import CustomerEditorDialog
from app.gui.widgets.buttons import AppButton


class CustomerDetailsPanel(QWidget):
    fileActivated = Signal(str)
    customerChanged = Signal()

    def __init__(self, repository: CustomerRepository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.current_details: dict | None = None
        self.setObjectName("CustomerDetailsSection")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)
        title = QLabel("Ordner- & Kundendetails")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        info = QGridLayout()
        self.customer_name_label = self._value_label("-")
        self.file_count_label = self._value_label("0")
        self.size_label = self._value_label("0 B")
        self.modified_label = self._value_label("-")
        info.addWidget(self._caption("Ordner"), 0, 0, 1, 2)
        info.addWidget(self.customer_name_label, 1, 0, 1, 2)
        info.addWidget(self._caption("Dateien"), 2, 0)
        info.addWidget(self._caption("Größe"), 2, 1)
        info.addWidget(self.file_count_label, 3, 0)
        info.addWidget(self.size_label, 3, 1)
        info.addWidget(self._caption("Zuletzt geändert"), 4, 0, 1, 2)
        info.addWidget(self.modified_label, 5, 0, 1, 2)
        layout.addLayout(info)
        layout.addWidget(self._caption("Fachthema"))
        self.service_types_label = self._value_label("-")
        layout.addWidget(self.service_types_label)

        layout.addWidget(self._caption("Kundenstammdaten"))
        self.customer_summary = QLabel("Noch kein Kundeneintrag verknüpft")
        self.customer_summary.setObjectName("CustomerSummary")
        self.customer_summary.setWordWrap(True)
        layout.addWidget(self.customer_summary)
        self.edit_button = AppButton("Kundendaten anlegen", AppButton.SECONDARY)
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self.edit_customer)
        layout.addWidget(self.edit_button)

        layout.addWidget(self._caption("Dateien"))
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(self._activate_file)
        layout.addWidget(self.file_list, 1)

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("StatCaption")
        return label

    def _value_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("StatValue")
        label.setWordWrap(True)
        return label

    def set_folder(self, details: dict):
        self.current_details = details
        self.customer_name_label.setText(details["folder_name"])
        self.file_count_label.setText(str(details["file_count"]))
        self.size_label.setText(f"{details['total_size'] / 1024 / 1024:.2f} MB")
        if details.get("last_modified"):
            self.modified_label.setText(
                datetime.fromisoformat(details["last_modified"]).strftime("%d.%m.%Y %H:%M")
            )
        else:
            self.modified_label.setText("-")
        self.service_types_label.setText(", ".join(details.get("service_types", [])) or "-")
        self.file_list.clear()
        for file_info in details.get("files", []):
            item = QListWidgetItem(file_info["filename"])
            item.setData(Qt.UserRole, file_info["path"])
            item.setToolTip(file_info["path"])
            self.file_list.addItem(item)
        self.edit_button.setEnabled(True)
        self._refresh_customer_summary()

    def _refresh_customer_summary(self):
        if self.current_details is None:
            return
        customer = self.repository.get_by_folder(self.current_details["folder_path"])
        if customer is None:
            self.customer_summary.setText("Noch kein Kundeneintrag verknüpft")
            self.edit_button.setText("Kundendaten anlegen")
            return
        lines = [customer.display_name]
        if customer.company and customer.company != customer.display_name:
            lines.append(customer.company)
        contact = " · ".join(value for value in (customer.email, customer.phone) if value)
        if contact:
            lines.append(contact)
        if customer.tags:
            lines.append("Tags: " + ", ".join(customer.tags))
        if customer.service_types:
            lines.append("Dienstleistungen: " + ", ".join(customer.service_types))
        lines.append(f"{len(customer.contacts)} Kontakte · {len(customer.notes)} Notizen")
        self.customer_summary.setText("\n".join(lines))
        self.edit_button.setText("Kundendaten bearbeiten")

    def edit_customer(self):
        if self.current_details is None:
            return
        dialog = CustomerEditorDialog(
            repository=self.repository,
            folder_path=self.current_details["folder_path"],
            suggested_name=self.current_details["folder_name"],
            parent=self,
        )
        if dialog.exec():
            self._refresh_customer_summary()
            self.customerChanged.emit()

    def _activate_file(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path:
            self.fileActivated.emit(str(path))
