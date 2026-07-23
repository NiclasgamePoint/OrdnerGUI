from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.customer_models import CustomerDataSuggestion
from app.core.config import CustomerRecognitionOptions
from app.core.customer_repository import CustomerRepository
from app.gui.dialogs.centered_popup import CenteredPopupDialog
from app.gui.widgets.buttons import AppButton
from app.gui.workers import ContactScanWorker


FIELD_LABELS = {
    "company": "Unternehmen",
    "contact_name": "Kontaktname",
    "email": "E-Mail-Adresse",
    "phone": "Telefonnummer",
    "street": "Straße und Hausnummer",
    "postal_code": "Postleitzahl",
    "city": "Ort",
    "entity_type": "Kundentyp",
}


class CustomerDataSuggestionsDialog(CenteredPopupDialog):
    suggestionsChanged = Signal(int)
    customerChanged = Signal(int)

    def __init__(
        self,
        repository: CustomerRepository,
        customer_id: int,
        index_path: Path,
        recognition_options: CustomerRecognitionOptions,
        parent=None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.customer_id = customer_id
        self.index_path = index_path
        self.recognition_options = recognition_options
        self.scan_worker: ContactScanWorker | None = None
        self.resize(760, 600)
        self.setMinimumSize(620, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("RecognitionReviewBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Kontaktdaten prüfen")
        title.setObjectName("PopupSectionTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Diese Kunden- oder Kontaktdaten waren nicht sicher genug. "
            "Du kannst jedes Feld prüfen und bestätigen oder ändern."
        )
        hint.setObjectName("PopupCaption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setObjectName("PageScrollArea")
        scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.content.setObjectName("ThemedScrollContent")
        self.suggestion_layout = QVBoxLayout(self.content)
        self.suggestion_layout.setContentsMargins(4, 4, 8, 4)
        self.suggestion_layout.setSpacing(10)
        scroll.setWidget(self.content)
        layout.addWidget(scroll, 1)

        self.scan_result = QLabel("")
        self.scan_result.setObjectName("PopupCaption")
        self.scan_result.setWordWrap(True)
        layout.addWidget(self.scan_result)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.close_button = AppButton("Schließen")
        self.close_button.clicked.connect(self.accept)
        self.scan_button = AppButton(
            "Kontaktdaten neu suchen", AppButton.SECONDARY
        )
        self.scan_button.clicked.connect(self._start_scan)
        action_row.addWidget(self.scan_button)
        action_row.addWidget(self.close_button)
        layout.addLayout(action_row)
        root.addWidget(body)
        self._reload()

    def _reload(self):
        while self.suggestion_layout.count():
            item = self.suggestion_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        suggestions = self.repository.list_data_suggestions(self.customer_id)
        if not suggestions:
            empty = QLabel("Keine Kunden- oder Kontaktdaten mehr zu prüfen.")
            empty.setObjectName("SearchSectionMessage")
            self.suggestion_layout.addWidget(empty)
        for suggestion in suggestions:
            self.suggestion_layout.addWidget(self._suggestion_card(suggestion))
        self.suggestion_layout.addStretch(1)
        self.suggestionsChanged.emit(len(suggestions))

    def _suggestion_card(self, suggestion: CustomerDataSuggestion) -> QWidget:
        card = QFrame()
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        if suggestion.is_contact:
            heading_text = f"Ansprechpartner: {suggestion.contact_name}"
        else:
            heading_text = (
                f"{FIELD_LABELS.get(suggestion.field_name, suggestion.field_name)}: "
                f"{suggestion.suggested_value}"
            )
        heading = QLabel(heading_text)
        heading.setObjectName("SectionTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        entity_type_combo = None
        if suggestion.field_name == "entity_type":
            entity_type_combo = QComboBox()
            entity_type_combo.setAccessibleName("Kundentyp auswählen")
            for entity_type in ("Privatperson", "Unternehmen", "Organisation"):
                entity_type_combo.addItem(entity_type, entity_type)
            customer = self.repository.get(self.customer_id)
            selected_type = (
                customer.entity_type
                if customer is not None
                else suggestion.suggested_value
            )
            entity_type_combo.setCurrentIndex(
                max(0, entity_type_combo.findData(selected_type))
            )
            layout.addWidget(entity_type_combo)
        if suggestion.is_contact:
            details = [
                ("Rolle", suggestion.contact_role),
                ("E-Mail", suggestion.contact_email),
                ("Telefon", suggestion.contact_phone),
            ]
            for label, value in details:
                if value:
                    detail = QLabel(f"{label}: {value}")
                    detail.setObjectName("PopupCaption")
                    detail.setWordWrap(True)
                    layout.addWidget(detail)
            conflict = self._contact_conflict_text(suggestion)
            if conflict:
                warning = QLabel(conflict)
                warning.setObjectName("PopupWarning")
                warning.setWordWrap(True)
                layout.addWidget(warning)
        confidence = QLabel(f"Sicherheit: {suggestion.confidence:.0%}")
        confidence.setObjectName("PopupCaption")
        layout.addWidget(confidence)
        if suggestion.rule:
            rule = QLabel(f"Begründung: {suggestion.rule}")
            rule.setObjectName("PopupCaption")
            rule.setWordWrap(True)
            layout.addWidget(rule)
        if suggestion.source_path:
            source = QLabel(f"Quelle: {Path(suggestion.source_path).name}")
            source.setToolTip(suggestion.source_path)
            source.setObjectName("PopupCaption")
            source.setWordWrap(True)
            layout.addWidget(source)
        if suggestion.excerpt:
            excerpt = QLabel(f"Textausschnitt: {suggestion.excerpt}")
            excerpt.setObjectName("PopupCaption")
            excerpt.setWordWrap(True)
            layout.addWidget(excerpt)
        actions = QHBoxLayout()
        actions.addStretch(1)
        if entity_type_combo is not None:
            save = AppButton("Kundentyp speichern")
            save.clicked.connect(
                lambda: self._resolve(
                    suggestion,
                    True,
                    str(entity_type_combo.currentData()),
                )
            )
            actions.addWidget(save)
            layout.addLayout(actions)
            return card
        reject = AppButton("Ablehnen", AppButton.DANGER)
        accept = AppButton("Annehmen")
        reject.clicked.connect(lambda: self._resolve(suggestion, False))
        accept.clicked.connect(lambda: self._resolve(suggestion, True))
        actions.addWidget(reject)
        actions.addWidget(accept)
        layout.addLayout(actions)
        return card

    def _resolve(
        self,
        suggestion: CustomerDataSuggestion,
        accept: bool,
        accepted_value: str | None = None,
    ):
        if (
            accept
            and suggestion.field_name != "entity_type"
            and self._would_overwrite(suggestion)
        ):
            answer = QMessageBox.question(
                self,
                "Vorhandenen Wert ersetzen?",
                "Für dieses Feld ist bereits ein anderer Wert gespeichert. "
                f"Soll er durch „{suggestion.suggested_value}“ ersetzt werden?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        try:
            customer = self.repository.resolve_data_suggestion(
                int(suggestion.id),
                accept,
                accepted_value,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Kontaktdaten prüfen", str(error))
            self._reload()
            return
        if accept and customer.id is not None:
            self.customerChanged.emit(int(customer.id))
        self._reload()

    def _would_overwrite(self, suggestion: CustomerDataSuggestion) -> bool:
        if suggestion.is_contact:
            return False
        if suggestion.field_name == "contact_name":
            return False
        customer = self.repository.get(self.customer_id)
        if customer is None:
            return False
        current = str(getattr(customer, suggestion.field_name, "") or "").strip()
        return bool(current and current.casefold() != suggestion.suggested_value.casefold())

    def _contact_conflict_text(self, suggestion: CustomerDataSuggestion) -> str:
        customer = self.repository.get(self.customer_id)
        if customer is None:
            return ""
        proposed = suggestion.contact
        for existing in customer.contacts:
            same_name = (
                existing.name.strip().casefold()
                == proposed.name.strip().casefold()
            )
            same_email = bool(
                proposed.email
                and existing.email.strip().casefold() == proposed.email.casefold()
            )
            if not (same_name or same_email):
                continue
            conflicts = []
            for label, current, new in (
                ("Rolle", existing.role, proposed.role),
                ("E-Mail", existing.email, proposed.email),
                ("Telefon", existing.phone, proposed.phone),
            ):
                if current and new and current.casefold() != new.casefold():
                    conflicts.append(f"{label}: „{current}“ statt „{new}“")
            if conflicts:
                return (
                    "Vorhandene manuelle Angaben bleiben erhalten: "
                    + "; ".join(conflicts)
                )
        return ""

    def _start_scan(self):
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        self.scan_result.setText("Kontaktdaten werden im Suchindex geprüft …")
        self.scan_button.set_busy(True)
        self.close_button.setEnabled(False)
        self.scan_worker = ContactScanWorker(
            self.index_path,
            self.repository.database_path,
            self.recognition_options,
            self.customer_id,
            parent=self,
        )
        self.scan_worker.completed.connect(self._scan_finished)
        self.scan_worker.finished.connect(self.scan_worker.deleteLater)
        self.scan_worker.start()

    def _scan_finished(self, result, error: str):
        self.scan_button.set_busy(False)
        self.close_button.setEnabled(True)
        self.scan_worker = None
        if error:
            self.scan_result.setText(f"Suche fehlgeschlagen: {error}")
            return
        self._reload()
        self.customerChanged.emit(self.customer_id)
        self.scan_result.setText(
            f"{result.found_fields} Felder gefunden · "
            f"{result.applied_fields} sicher übernommen · "
            f"{result.pending_fields} jetzt zu prüfen"
        )

    def reject(self):
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        super().reject()
