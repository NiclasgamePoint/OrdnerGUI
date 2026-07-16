from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
)

from app.core.config import CustomerRecognitionOptions
from app.core.customer_recognition_models import RecognitionCandidate
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs.centered_popup import CenteredPopupDialog
from app.gui.widgets.buttons import AppButton
from app.services.customer_recognition import CustomerRecognitionService


class CustomerRecognitionReviewDialog(CenteredPopupDialog):
    casesChanged = Signal(int)
    customersChanged = Signal()

    def __init__(
        self,
        index_path: Path,
        customer_database_path: Path,
        options: CustomerRecognitionOptions,
        parent=None,
    ):
        super().__init__(parent)
        self.index_path = index_path
        self.customer_database_path = customer_database_path
        self.options = options
        self._cases: dict[str, RecognitionCandidate] = {}
        self._customer_labels: dict[int, str] = {}
        self.resize(940, 640)
        self.setMinimumSize(760, 520)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("RecognitionReviewBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Automatische Kundenerkennung prüfen")
        title.setObjectName("PopupSectionTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Nur mehrdeutige Fälle werden hier angezeigt. Bereits vorhandene "
            "Kundeneinträge werden niemals miteinander verschmolzen."
        )
        hint.setObjectName("PopupCaption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)
        self.case_list = QListWidget()
        self.case_list.setObjectName("RecognitionCaseList")
        self.case_list.currentItemChanged.connect(self._show_selected_case)
        splitter.addWidget(self.case_list)

        details_panel = QFrame()
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(12, 0, 0, 0)
        self.case_title = QLabel("Kein Prüffall ausgewählt")
        self.case_title.setObjectName("PopupTitle")
        details_layout.addWidget(self.case_title)
        self.case_details = QPlainTextEdit()
        self.case_details.setReadOnly(True)
        details_layout.addWidget(self.case_details, 1)

        customer_label = QLabel("Vorhandenem Kunden zuordnen")
        customer_label.setObjectName("PopupCaption")
        details_layout.addWidget(customer_label)
        self.customer_combo = QComboBox()
        self.customer_combo.setMinimumWidth(280)
        details_layout.addWidget(self.customer_combo)

        action_row = QHBoxLayout()
        self.assign_button = AppButton("Ordner zuordnen")
        self.together_button = AppButton("Gemeinsam neu anlegen", AppButton.SECONDARY)
        self.separate_button = AppButton("Getrennt anlegen", AppButton.SECONDARY)
        self.ignore_button = AppButton("Nicht verarbeiten", AppButton.DANGER)
        self.assign_button.clicked.connect(lambda: self._resolve("assign"))
        self.together_button.clicked.connect(lambda: self._resolve("together"))
        self.separate_button.clicked.connect(lambda: self._resolve("separate"))
        self.ignore_button.clicked.connect(lambda: self._resolve("ignore"))
        action_row.addWidget(self.assign_button)
        action_row.addWidget(self.together_button)
        action_row.addWidget(self.separate_button)
        action_row.addWidget(self.ignore_button)
        details_layout.addLayout(action_row)
        splitter.addWidget(details_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = AppButton("Schließen", AppButton.SECONDARY)
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)
        root_layout.addWidget(body)

        self._load_customers()
        self._reload_cases()

    def _repository(self) -> CustomerRepository:
        return CustomerRepository(self.customer_database_path)

    def _load_customers(self):
        repository = self._repository()
        try:
            customers = repository.list_customers()
        finally:
            repository.close()
        selected = self.customer_combo.currentData()
        self.customer_combo.clear()
        self._customer_labels.clear()
        self.customer_combo.addItem("Kunden auswählen …", None)
        for customer in customers:
            context = " · ".join(
                value for value in (customer.city, customer.entity_type) if value
            )
            label = f"{customer.display_name} · {context}" if context else customer.display_name
            self.customer_combo.addItem(label, customer.id)
            if customer.id is not None:
                self._customer_labels[int(customer.id)] = label
        if selected is not None:
            index = self.customer_combo.findData(selected)
            self.customer_combo.setCurrentIndex(max(0, index))

    def _reload_cases(self):
        repository = self._repository()
        try:
            cases = repository.list_pending_recognition_cases()
        finally:
            repository.close()
        self._cases = {candidate.signature: candidate for candidate in cases}
        self.case_list.clear()
        for candidate in cases:
            item = QListWidgetItem(
                f"{candidate.display_name} · {candidate.city or 'Ort unbekannt'}"
            )
            item.setData(Qt.UserRole, candidate.signature)
            item.setToolTip("\n".join(candidate.folder_paths))
            self.case_list.addItem(item)
        self.casesChanged.emit(len(cases))
        if cases:
            self.case_list.setCurrentRow(0)
        else:
            self.case_title.setText("Keine offenen Prüffälle")
            self.case_details.setPlainText(
                "Alle mehrdeutigen Erkennungen wurden bearbeitet."
            )
            self._set_actions_enabled(False)

    def _selected_candidate(self) -> RecognitionCandidate | None:
        item = self.case_list.currentItem()
        signature = str(item.data(Qt.UserRole) or "") if item else ""
        return self._cases.get(signature)

    def _show_selected_case(self, current, _previous=None):
        candidate = self._selected_candidate()
        if candidate is None:
            self._set_actions_enabled(False)
            return
        self.case_title.setText(
            f"{candidate.display_name} · {candidate.city or 'Ort unbekannt'}"
        )
        lines = [
            f"Grund: {candidate.reason or 'Mehrdeutige Erkennung'}",
            f"Dienstleistungen: {', '.join(candidate.service_types) or '-'}",
            f"Jahre: {', '.join(str(value) for value in candidate.years) or '-'}",
            f"E-Mail: {candidate.email or '-'}",
            f"Telefon: {candidate.phone or '-'}",
            f"Adresse: {' '.join(value for value in (candidate.street, candidate.postal_code, candidate.city) if value) or '-'}",
            "",
            "Projektordner:",
            *(f"  {path}" for path in candidate.folder_paths),
        ]
        if candidate.suggested_customer_ids:
            lines.extend([
                "",
                "Mögliche Bestandskunden:",
                *(
                    f"  {self._customer_labels.get(value, f'Kunde #{value}')}"
                    for value in candidate.suggested_customer_ids
                ),
            ])
            suggested_index = self.customer_combo.findData(
                candidate.suggested_customer_ids[0]
            )
            if suggested_index >= 0:
                self.customer_combo.setCurrentIndex(suggested_index)
        else:
            self.customer_combo.setCurrentIndex(0)
        self.case_details.setPlainText("\n".join(lines))
        self.separate_button.setEnabled(len(candidate.folder_paths) > 1)
        self._set_actions_enabled(True, keep_separate_state=True)

    def _set_actions_enabled(self, enabled: bool, keep_separate_state: bool = False):
        self.assign_button.setEnabled(enabled)
        self.together_button.setEnabled(enabled)
        self.ignore_button.setEnabled(enabled)
        if not keep_separate_state:
            self.separate_button.setEnabled(enabled)

    def _resolve(self, action: str):
        candidate = self._selected_candidate()
        if candidate is None:
            return
        customer_id = self.customer_combo.currentData() if action == "assign" else None
        if action == "assign" and customer_id is None:
            QMessageBox.warning(
                self, "Kundenerkennung", "Bitte einen vorhandenen Kunden auswählen."
            )
            return
        try:
            CustomerRecognitionService(
                self.index_path,
                self.customer_database_path,
                self.options,
            ).resolve_case(candidate, action, customer_id)
        except Exception as error:
            QMessageBox.warning(self, "Prüffall konnte nicht gespeichert werden", str(error))
            return
        self.customersChanged.emit()
        self._load_customers()
        self._reload_cases()
