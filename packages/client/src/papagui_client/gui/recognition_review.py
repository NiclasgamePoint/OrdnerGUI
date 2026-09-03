"""Server-backed recognition review in the original 0.4.1 dialog design."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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

from .dialogs.centered_popup import CenteredPopupDialog
from .widgets.buttons import AppButton


class RecognitionReviewDialog(CenteredPopupDialog):
    """Review ambiguous server cases without importing server implementation."""

    casesChanged = Signal(int)
    customersChanged = Signal()

    def __init__(self, server_control, customers=(), parent=None):
        super().__init__(parent)
        self._control = server_control
        self._customer_views = tuple(customers)
        self._cases = ()
        self._customers: list[tuple[int, str, str, object]] = []
        self.setWindowTitle("Kundenerkennung prüfen")
        self.resize(1100, 680)
        self.setMinimumSize(980, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("RecognitionReviewBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("Automatische Kundenerkennung prüfen")
        title.setObjectName("PopupSectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.status = QLabel("Noch nicht geladen")
        self.status.setObjectName("PopupCaption")
        title_row.addWidget(self.status)
        layout.addLayout(title_row)

        hint = QLabel(
            "Gleiche Kundennamen werden automatisch zusammengeführt. Hier werden "
            "nur ähnliche Namen und widersprüchliche Ordnerzuordnungen angezeigt."
        )
        hint.setObjectName("PopupCaption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.case_list = QListWidget()
        self.case_list.setObjectName("RecognitionCaseList")
        self.case_list.setMinimumWidth(360)
        self.case_list.setWordWrap(True)
        self.case_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.case_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.case_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.case_list.setAccessibleName("Offene Kundenerkennungs-Prüffälle")
        self.case_list.currentRowChanged.connect(self._show_case)
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
        for entity_type in ("Privatperson", "Unternehmen", "Organisation"):
            self.entity_type_combo.addItem(entity_type, entity_type)
        self.entity_type_combo.setToolTip(
            "Der Kundentyp wird aktuell vom Serverdatensatz übernommen."
        )
        details_layout.addWidget(self.entity_type_combo)

        customer_label = QLabel("Vorhandenem Kunden zuordnen")
        customer_label.setObjectName("PopupCaption")
        details_layout.addWidget(customer_label)
        self.customer_search = QLineEdit()
        self.customer_search.setPlaceholderText(
            "Kunden nach Name, Ort oder Kundentyp suchen …"
        )
        self.customer_search.setClearButtonEnabled(True)
        self.customer_search.textChanged.connect(self._filter_customers)
        details_layout.addWidget(self.customer_search)
        self.customer_list = QListWidget()
        self.customer_list.setObjectName("RecognitionCustomerList")
        self.customer_list.setMinimumWidth(280)
        self.customer_list.setMinimumHeight(110)
        self.customer_list.currentItemChanged.connect(self._customer_selected)
        details_layout.addWidget(self.customer_list)

        actions = QHBoxLayout()
        self.assign_button = AppButton("Ordner zuordnen")
        self.together_button = AppButton(
            "Gemeinsam neu anlegen", AppButton.SECONDARY
        )
        self.separate_button = AppButton("Getrennt anlegen", AppButton.SECONDARY)
        self.ignore_button = AppButton("Nicht verarbeiten", AppButton.DANGER)
        self.assign_button.clicked.connect(lambda: self.decide("assign"))
        self.together_button.clicked.connect(lambda: self.decide("accept"))
        self.separate_button.clicked.connect(self._separate_not_supported)
        self.ignore_button.clicked.connect(lambda: self.decide("reject"))
        actions.addWidget(self.assign_button)
        actions.addWidget(self.together_button)
        actions.addWidget(self.separate_button)
        actions.addWidget(self.ignore_button)
        details_layout.addLayout(actions)

        splitter.addWidget(details_panel)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes((380, 680))
        self.splitter = splitter
        layout.addWidget(splitter, 1)

        close_row = QHBoxLayout()
        self.reload_button = AppButton("Aktualisieren", AppButton.SECONDARY)
        self.reload_button.clicked.connect(self.reload)
        self.run_button = AppButton(
            "Erkennung jetzt starten", AppButton.SECONDARY
        )
        self.run_button.clicked.connect(self.start_run)
        close_row.addWidget(self.reload_button)
        close_row.addWidget(self.run_button)
        close_row.addStretch(1)
        close_button = AppButton("Schließen", AppButton.SECONDARY)
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)
        root.addWidget(body)

        # Compatibility aliases kept from the first 0.4.2 client UI.
        self.cases = self.case_list
        self.details = self.case_details
        self.customer = QComboBox(self)
        self.customer.hide()
        self._load_customers()
        self.reload()

    def _load_customers(self) -> None:
        selected_id = self._selected_customer_id()
        self._customers.clear()
        self.customer.clear()
        self.customer.addItem("Zielkunde wählen …", None)
        for view in self._customer_views:
            customer = view.customer
            if customer.id is None:
                continue
            context = " · ".join(
                value for value in (customer.city, customer.entity_type) if value
            )
            label = (
                f"{customer.display_name} · {context}"
                if context
                else customer.display_name
            )
            search_value = " ".join(
                value
                for value in (
                    customer.display_name,
                    customer.city,
                    customer.entity_type,
                )
                if value
            ).casefold()
            self._customers.append((int(customer.id), label, search_value, view))
            self.customer.addItem(label, view)
        self._filter_customers()
        if selected_id is not None:
            self._select_customer(selected_id)

    def _filter_customers(self, search_text: str = "") -> None:
        selected = self._selected_customer_id()
        query = search_text.strip().casefold()
        self.customer_list.clear()
        matches = [item for item in self._customers if not query or query in item[2]]
        if not matches:
            message = (
                "Noch keine Kunden vorhanden."
                if not self._customers
                else "Keine passenden Kunden gefunden."
            )
            item = QListWidgetItem(message)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.customer_list.addItem(item)
            return
        for customer_id, label, _search, _view in matches:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, customer_id)
            self.customer_list.addItem(item)
        if selected is not None:
            self._select_customer(selected)

    def _customer_selected(self, current, _previous=None) -> None:
        customer_id = (
            current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        )
        if customer_id is None:
            return
        for index in range(1, self.customer.count()):
            view = self.customer.itemData(index)
            if view is not None and view.customer.id == customer_id:
                self.customer.blockSignals(True)
                self.customer.setCurrentIndex(index)
                self.customer.blockSignals(False)
                return

    def _selected_customer_id(self) -> int | None:
        # The hidden combo is part of the public API and may be changed by callers.
        view = self.customer.currentData() if hasattr(self, "customer") else None
        if view is not None and view.customer.id is not None:
            return int(view.customer.id)
        item = self.customer_list.currentItem() if hasattr(self, "customer_list") else None
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return int(value) if value is not None else None

    def _selected_customer_view(self):
        customer_id = self._selected_customer_id()
        if customer_id is None:
            return None
        for current_id, _label, _search, view in self._customers:
            if current_id == customer_id:
                return view
        return None

    def _select_customer(self, customer_id: int) -> bool:
        found = False
        for row in range(self.customer_list.count()):
            item = self.customer_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == customer_id:
                self.customer_list.setCurrentItem(item)
                self.customer_list.scrollToItem(item)
                found = True
                break
        if not found:
            return False
        for index in range(1, self.customer.count()):
            view = self.customer.itemData(index)
            if view is not None and view.customer.id == customer_id:
                self.customer.setCurrentIndex(index)
                break
        return True

    def reload(self) -> None:
        try:
            self._cases = tuple(self._control.recognition_cases("pending"))
            runs = tuple(self._control.recognition_runs(10))
        except Exception as exc:
            self.status.setText(f"Nicht verfügbar: {exc}")
            return
        self.case_list.clear()
        for case in self._cases:
            city = " · ".join(case.cities) or "Ort unbekannt"
            item = QListWidgetItem(f"{case.display_name} · {city}")
            item.setData(Qt.ItemDataRole.UserRole, case.signature)
            item.setToolTip(
                "\n".join(
                    f"{source.source_id}:{source.relative_path}"
                    for source in case.project_roots
                )
            )
            self.case_list.addItem(item)
        self.casesChanged.emit(len(self._cases))
        last = runs[0] if runs else None
        suffix = f" · letzter Lauf: {last.started_at}" if last is not None else ""
        self.status.setText(f"{len(self._cases)} offene Fälle{suffix}")
        if self._cases:
            self.case_list.setCurrentRow(0)
        else:
            self.case_title.setText("Keine offenen Prüffälle")
            self.case_details.setPlainText(
                "Keine offenen Erkennungsfälle. Alle mehrdeutigen Erkennungen "
                "wurden bearbeitet."
            )
            self._set_actions_enabled(False)

    def selected_case(self):
        row = self.case_list.currentRow()
        return self._cases[row] if 0 <= row < len(self._cases) else None

    def _show_case(self, row: int) -> None:
        if not 0 <= row < len(self._cases):
            self.case_details.clear()
            self._set_actions_enabled(False)
            return
        case = self._cases[row]
        cities = ", ".join(case.cities) or "Ort unbekannt"
        self.case_title.setText(f"{case.display_name} · {cities}")
        lines = [
            f"Grund: {case.reason or 'Mehrdeutige Erkennung'}",
            f"Dienstleistungen: {', '.join(case.service_types) or '-'}",
            f"Jahre: {', '.join(str(value) for value in case.years) or '-'}",
            "",
            "Projektordner:",
            *(
                f"  {source.source_id}:{source.relative_path}"
                for source in case.project_roots
            ),
        ]
        if case.evidence:
            lines.extend(("", "Erkennungsbelege:"))
            for evidence in sorted(
                case.evidence, key=lambda item: (-item.confidence, item.field_name)
            ):
                lines.extend(
                    (
                        f"  {evidence.field_name}: {evidence.value} "
                        f"({evidence.confidence:.0%})",
                        f"    Regel: {evidence.rule or '-'}",
                        "    Quelle: "
                        f"{evidence.source.source_id}:{evidence.source.relative_path}",
                        f"    Kontext: {evidence.excerpt or '-'}",
                    )
                )
        self.case_details.setPlainText("\n".join(lines))
        self.customer_search.clear()
        if case.suggested_customer_ids:
            self._select_customer(case.suggested_customer_ids[0])
        else:
            self.customer_list.clearSelection()
            self.customer_list.setCurrentItem(None)
            self.customer.setCurrentIndex(0)
        self._set_actions_enabled(True)
        # Splitting one server case into several customers needs an explicit API.
        self.separate_button.setEnabled(False)
        self.separate_button.setToolTip(
            "Für diese Aktion fehlt noch ein eigener Server-API-Vertrag."
        )

    def start_run(self) -> None:
        if not self._ensure_admin():
            return
        try:
            summary = self._control.start_recognition()
        except Exception as exc:
            QMessageBox.warning(self, "Erkennung fehlgeschlagen", str(exc))
            return
        self.status.setText(
            f"Erkennung beendet: {summary.detected} erkannt, {summary.pending} offen"
        )
        self.reload()

    def decide(self, action: str) -> None:
        case = self.selected_case()
        if case is None or not self._ensure_admin():
            return
        customer_id = None
        expected_revision = None
        if action == "accept":
            expected_revision = 0
        elif action == "assign":
            view = self.customer.currentData()
            if view is None or view.customer.id is None:
                QMessageBox.warning(
                    self, "Zielkunde fehlt", "Bitte einen Zielkunden wählen."
                )
                return
            customer_id = view.customer.id
            expected_revision = view.customer.revision
        try:
            self._control.decide_recognition(
                case.signature, action, customer_id, expected_revision
            )
        except Exception as exc:
            QMessageBox.warning(self, "Entscheidung fehlgeschlagen", str(exc))
            return
        self.customersChanged.emit()
        self.reload()

    def _separate_not_supported(self) -> None:
        QMessageBox.information(
            self,
            "Getrennt anlegen",
            "Das alte Bedienfeld ist wieder vorhanden. Für das getrennte Anlegen "
            "muss der Server noch einen transaktionalen API-Endpunkt bereitstellen.",
        )

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.entity_type_combo.setEnabled(enabled)
        self.assign_button.setEnabled(enabled)
        self.together_button.setEnabled(enabled)
        self.ignore_button.setEnabled(enabled)
        self.separate_button.setEnabled(False)

    def _ensure_admin(self) -> bool:
        if self._control.admin_unlocked:
            return True
        password, accepted = QInputDialog.getText(
            self,
            "Admin entsperren",
            "Adminpasswort",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return False
        try:
            self._control.login_admin(password)
        except Exception as exc:
            QMessageBox.warning(self, "Anmeldung fehlgeschlagen", str(exc))
            return False
        return True


# v0.4.1 class name remains importable for extensions and documentation links.
CustomerRecognitionReviewDialog = RecognitionReviewDialog
