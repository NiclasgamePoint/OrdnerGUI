from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
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
        remote_resolver: Callable[[RecognitionCandidate, str, int | None], object]
        | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.index_path = index_path
        self.customer_database_path = customer_database_path
        self.options = options
        self.remote_resolver = remote_resolver
        self._cases: dict[str, RecognitionCandidate] = {}
        self._customer_labels: dict[int, str] = {}
        self._customers: list[tuple[int, str, str]] = []
        self.resize(1100, 680)
        self.setMinimumSize(980, 520)

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
            "Gleiche Kundennamen werden automatisch zusammengeführt. Hier werden "
            "nur ähnliche Namen und widersprüchliche Ordnerzuordnungen angezeigt."
        )
        hint.setObjectName("PopupCaption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)
        self.case_list = QListWidget()
        self.case_list.setObjectName("RecognitionCaseList")
        self.case_list.setMinimumWidth(360)
        self.case_list.setWordWrap(True)
        self.case_list.setTextElideMode(Qt.ElideNone)
        self.case_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.case_list.setResizeMode(QListView.Adjust)
        self.case_list.setAccessibleName("Offene Kundenerkennungs-Prüffälle")
        self.case_list.setAccessibleDescription(
            "Liste mehrdeutiger automatisch erkannter Kundenordner."
        )
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

        entity_type_label = QLabel("Kundentyp bestätigen")
        entity_type_label.setObjectName("PopupCaption")
        details_layout.addWidget(entity_type_label)
        self.entity_type_combo = QComboBox()
        self.entity_type_combo.setAccessibleName("Kundentyp bestätigen")
        for entity_type in ("Privatperson", "Unternehmen", "Organisation"):
            self.entity_type_combo.addItem(entity_type, entity_type)
        details_layout.addWidget(self.entity_type_combo)

        customer_label = QLabel("Vorhandenem Kunden zuordnen")
        customer_label.setObjectName("PopupCaption")
        details_layout.addWidget(customer_label)
        self.customer_search = QLineEdit()
        self.customer_search.setPlaceholderText(
            "Kunden nach Name, Ort oder Kundentyp suchen …"
        )
        self.customer_search.setClearButtonEnabled(True)
        self.customer_search.setAccessibleName("Bestandskunden suchen")
        self.customer_search.setAccessibleDescription(
            "Filtert die Kundenliste nach Name, Ort oder Kundentyp."
        )
        self.customer_search.textChanged.connect(self._filter_customers)
        details_layout.addWidget(self.customer_search)
        self.customer_list = QListWidget()
        self.customer_list.setObjectName("RecognitionCustomerList")
        self.customer_list.setMinimumWidth(280)
        self.customer_list.setMinimumHeight(110)
        self.customer_list.setAccessibleName("Gefundene Bestandskunden")
        details_layout.addWidget(self.customer_list)

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
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([380, 680])
        self.splitter = splitter
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
        selected = self._selected_customer_id()
        self._customers.clear()
        self._customer_labels.clear()
        for customer in customers:
            context = " · ".join(
                value for value in (customer.city, customer.entity_type) if value
            )
            label = f"{customer.display_name} · {context}" if context else customer.display_name
            if customer.id is not None:
                customer_id = int(customer.id)
                search_text = " ".join(
                    value
                    for value in (
                        customer.display_name,
                        customer.city,
                        customer.entity_type,
                    )
                    if value
                ).casefold()
                self._customers.append((customer_id, label, search_text))
                self._customer_labels[customer_id] = label
        self._filter_customers()
        if selected is not None:
            self._select_customer(selected)

    def _filter_customers(self, search_text: str = ""):
        selected = self._selected_customer_id()
        query = search_text.strip().casefold()
        self.customer_list.clear()
        matches = [
            customer
            for customer in self._customers
            if not query or query in customer[2]
        ]
        if not matches:
            message = (
                "Noch keine Kunden vorhanden."
                if not self._customers
                else "Keine passenden Kunden gefunden."
            )
            item = QListWidgetItem(message)
            item.setFlags(Qt.NoItemFlags)
            self.customer_list.addItem(item)
            return
        for customer_id, label, _search_value in matches:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, customer_id)
            self.customer_list.addItem(item)
        if selected is not None:
            self._select_customer(selected)

    def _selected_customer_id(self) -> int | None:
        item = self.customer_list.currentItem()
        value = item.data(Qt.UserRole) if item is not None else None
        return int(value) if value is not None else None

    def _select_customer(self, customer_id: int) -> bool:
        for row in range(self.customer_list.count()):
            item = self.customer_list.item(row)
            if item.data(Qt.UserRole) == customer_id:
                self.customer_list.setCurrentItem(item)
                self.customer_list.scrollToItem(item)
                return True
        return False

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
        if candidate.evidence:
            lines.extend(["", "Erkennungsbelege:"])
            for evidence in sorted(
                candidate.evidence, key=lambda item: (-item.confidence, item.field_name)
            ):
                status = "sicher" if evidence.automatic else "Vorschlag"
                lines.extend([
                    f"  {evidence.field_name}: {evidence.value} "
                    f"({status}, {evidence.confidence:.0%})",
                    f"    Regel: {evidence.rule}",
                    f"    Quelle: {evidence.source_path}",
                    f"    Kontext: {evidence.excerpt}",
                ])
        self.customer_search.clear()
        if candidate.suggested_customer_ids:
            lines.extend([
                "",
                "Mögliche Bestandskunden:",
                *(
                    f"  {self._customer_labels.get(value, f'Kunde #{value}')}"
                    for value in candidate.suggested_customer_ids
                ),
            ])
            self._select_customer(candidate.suggested_customer_ids[0])
        else:
            self.customer_list.clearSelection()
            self.customer_list.setCurrentItem(None)
        self.case_details.setPlainText("\n".join(lines))
        entity_type_index = self.entity_type_combo.findData(
            candidate.entity_type or "Privatperson"
        )
        self.entity_type_combo.setCurrentIndex(max(0, entity_type_index))
        self.separate_button.setEnabled(len(candidate.folder_paths) > 1)
        self._set_actions_enabled(True, keep_separate_state=True)

    def _set_actions_enabled(self, enabled: bool, keep_separate_state: bool = False):
        self.entity_type_combo.setEnabled(enabled)
        self.assign_button.setEnabled(enabled)
        self.together_button.setEnabled(enabled)
        self.ignore_button.setEnabled(enabled)
        if not keep_separate_state:
            self.separate_button.setEnabled(enabled)

    def _resolve(self, action: str):
        candidate = self._selected_candidate()
        if candidate is None:
            return
        candidate.entity_type = str(
            self.entity_type_combo.currentData() or "Privatperson"
        )
        customer_id = self._selected_customer_id() if action == "assign" else None
        if action == "assign" and customer_id is None:
            QMessageBox.warning(
                self, "Kundenerkennung", "Bitte einen vorhandenen Kunden auswählen."
            )
            return
        try:
            if self.remote_resolver is not None:
                self.remote_resolver(candidate, action, customer_id)
            else:
                CustomerRecognitionService(
                    self.index_path,
                    self.customer_database_path,
                    self.options,
                ).resolve_case(candidate, action, customer_id)
        except Exception as error:
            QMessageBox.warning(self, "Prüffall konnte nicht gespeichert werden", str(error))
            return
        self.customersChanged.emit()
        if self.remote_resolver is not None:
            self.accept()
            return
        self._load_customers()
        self._reload_cases()
