"""Bounded, revision-aware review of server-recognized customer data."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePosixPath

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from papagui_contracts import Contact, Customer, SourcePath

from .dialogs.centered_popup import CenteredPopupDialog
from .widgets.buttons import AppButton


FIELD_LABELS = {
    "company": "Unternehmen",
    "contact_name": "Kontaktname",
    "email": "E-Mail-Adresse",
    "phone": "Telefonnummer",
    "street": "Straße und Hausnummer",
    "postal_code": "Postleitzahl",
    "city": "Ort",
    "entity_type": "Kundentyp",
    "address": "Anschrift",
}


def quality_label(suggestion) -> str:
    return {"strong": "Gute Belege", "review": "Zuordnung unklar"}.get(
        getattr(suggestion, "quality", "legacy"), "Älterer Vorschlag – Belege prüfen"
    )


def reason_label(reason: str) -> str:
    if reason.startswith("document-role:"):
        return "Rolle im Dokument: " + reason.partition(":")[2].capitalize()
    return {
        "customer-identity-in-block": "Kundenname steht im selben Textabschnitt",
        "explicit-customer-role": "Im Dokument ausdrücklich als Kunde bezeichnet",
        "customer-role-identity-unresolved": "Kundenrolle erkannt; Name nicht sicher zugeordnet",
        "party-unresolved": "Zugehörigkeit zum Kunden ist unklar",
        "valid-phone-plan": "Telefonnummer hat ein gültiges Format",
        "contact-label": "Als Kontaktangabe beschriftet",
        "unlabelled-phone": "Telefonnummer ohne eindeutige Beschriftung",
        "valid-email-syntax": "E-Mail-Adresse hat ein gültiges Format",
        "named-contact-in-same-block": "Name und Kontaktdaten stehen zusammen",
        "complete-address-in-same-block": "Vollständige Anschrift in einem Textabschnitt",
        "legal-form-in-customer-block": "Firma und Rechtsform beim Kunden erkannt",
        "legacy_decision_conflict": "Frühere Entscheidungen widersprechen sich",
        "ocr-quality-unverified": "Der gelesene Text ist unsicher; bitte die Quelle prüfen",
        "ocr-layout-unverified": "Die Position im Scan ist nicht ausreichend belegt",
        "incomplete-address-block": "Die Anschrift ist unvollständig",
        "postbox-address": "Postfachanschrift im zusammenhängenden Block",
        "non-domestic-address-format": "Ausländisches Adressformat; bitte prüfen",
    }.get(reason, reason if " " in reason else "Zuordnung anhand des Belegs prüfen")


def recognition_text(status: Mapping | None) -> str:
    status = status or {}
    summary = status.get("summary")
    if summary:
        return str(summary)
    reason = {
        "no_projects": "Diesem Kunden sind noch keine Projekte zugeordnet.",
        "no_documents": "In den zugeordneten Projekten wurden keine Dokumente gefunden.",
        "unsupported": "Die zugeordneten Dokumentformate werden derzeit nicht unterstützt.",
        "no_text": "Die Dokumente enthalten noch keinen lesbaren Text. Dokumente neu lesen ist möglich.",
        "no_candidates": "Keine neuen prüfbaren Kundendaten gefunden.",
    }.get(str(status.get("reason", "")))
    if reason and status.get("state") not in {"running", "error", "disabled"}:
        return reason
    return {
        "not_evaluated": "Dokumente wurden noch nicht geprüft.",
        "running": "Kundendaten werden auf dem Server geprüft …",
        "partial": "Dokumente wurden nur teilweise geprüft. Weitere Angaben können fehlen.",
        "complete": "Prüfung abgeschlossen.",
        "error": "Die Dokumentprüfung ist fehlgeschlagen. Erneut prüfen ist möglich.",
        "disabled": "Die Dokumentprüfung ist deaktiviert.",
    }.get(str(status.get("state", "")), "Für diesen Datenstand liegt kein Erkennungsstatus vor.")


class ReviewSourceButton(AppButton):
    """Keep source filenames readable without letting paths grow the dialog."""

    def __init__(self, text: str, path: str):
        super().__init__("", AppButton.SECONDARY)
        self._full_text = text
        self.setObjectName("ReviewSourceButton")
        self.setToolTip(path)
        self.setAccessibleName(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._elide()

    def minimumSizeHint(self):
        return QSize(80, super().minimumSizeHint().height())

    def sizeHint(self):
        return QSize(240, super().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(30, self.width() - 30)
        ))


class CustomerSuggestionsDialog(CenteredPopupDialog):
    decisionRequested = Signal(object, str, int)
    pageRequested = Signal(int)
    sourceRequested = Signal(object)
    recognitionRequested = Signal(str)
    manualResolutionRequested = Signal()

    PAGE_SIZE = 30

    def __init__(self, suggestions, revision: int, parent=None, customer: Customer | None = None):
        super().__init__(parent)
        self._suggestions = tuple(suggestions)
        self._revision = revision
        self._customer = customer
        self._total = len(self._suggestions)
        self._offset = 0
        self._has_more = False
        self._busy = False
        self._offline = False
        self._recognition = {}
        self._error = ""
        self._visible_per_field = 3
        self._rejection_reasons: dict[int, str] = {}
        self._expanded_evidence: set[int] = set()
        self.resize(900, 720)
        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)
        self.setWindowTitle("Kundendaten prüfen")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        body = QFrame()
        body.setObjectName("RecognitionReviewBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        title = self._label("Kundendaten prüfen", "ReviewTitle")
        heading.addWidget(title, 1)
        self.refresh_button = AppButton("Aktualisieren", AppButton.SECONDARY)
        self.refresh_button.clicked.connect(lambda: self._request_page(self._offset))
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        self.status_label = self._label("", "ReviewStatus")
        layout.addWidget(self.status_label)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("RecognitionReviewScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setMinimumWidth(0)
        layout.addWidget(self.scroll, 1)
        paging = QHBoxLayout()
        self.page_label = self._label("", "ReviewMeta")
        paging.addWidget(self.page_label, 1)
        self.previous_button = AppButton("Zurück", AppButton.SECONDARY)
        self.previous_button.clicked.connect(
            lambda: self._request_page(max(0, self._offset - self.PAGE_SIZE))
        )
        self.more_button = AppButton("Weitere anzeigen", AppButton.SECONDARY)
        self.more_button.clicked.connect(self._show_more)
        paging.addWidget(self.previous_button)
        paging.addWidget(self.more_button)
        layout.addLayout(paging)
        actions = QHBoxLayout()
        self.rescan_button = AppButton("Neu bewerten", AppButton.SECONDARY)
        self.rescan_button.setToolTip("Bereits gelesene Dokumenttexte erneut auf Kundendaten prüfen")
        self.rescan_button.clicked.connect(lambda: self.recognitionRequested.emit("reassess"))
        self.extract_button = AppButton("Neu auslesen", AppButton.SECONDARY)
        self.extract_button.setToolTip("Dokumente einschließlich Texterkennung erneut auslesen und prüfen")
        self.extract_button.clicked.connect(lambda: self.recognitionRequested.emit("extract"))
        actions.addWidget(self.rescan_button)
        actions.addWidget(self.extract_button)
        actions.addStretch(1)
        close = AppButton("Schließen", AppButton.SECONDARY)
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        root.addWidget(body)
        self._render()

    @staticmethod
    def _label(text: str, name: str = "PopupCaption") -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setMinimumWidth(0)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        label.setSizePolicy(policy)
        return label

    def showEvent(self, event):
        screen = self.parentWidget().screen() if self.parentWidget() else QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().adjusted(16, 16, -16, -16)
            self.setMinimumSize(min(560, available.width()), min(420, available.height()))
            self.resize(min(self.width(), available.width()), min(self.height(), available.height()))
        super().showEvent(event)

    def set_data(
        self,
        suggestions,
        revision,
        customer=None,
        *,
        total=None,
        offset=0,
        has_more=False,
        recognition=None,
        offline=False,
        error="",
    ) -> None:
        self._suggestions = tuple(suggestions)
        self._expanded_evidence.intersection_update(item.id for item in self._suggestions)
        self._revision = revision
        self._customer = customer
        self._total = len(self._suggestions) if total is None else total
        self._offset = offset
        self._has_more = has_more
        self._recognition = dict(recognition or {})
        self._offline = offline
        self._error = error
        self._visible_per_field = 3
        self._render()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.scroll.setEnabled(not busy)
        self.previous_button.setEnabled(not busy and self._offset > 0)
        self.refresh_button.setEnabled(not busy)
        self.more_button.setEnabled(not busy)
        can_run = not busy and not self._offline and self._recognition.get("state") != "running"
        self.rescan_button.setEnabled(can_run)
        self.extract_button.setEnabled(can_run)
        self._update_status()

    def _update_status(self) -> None:
        if self._busy:
            text = "Wird verarbeitet …"
        elif self._error:
            text = f"Kundendaten konnten nicht geladen oder verarbeitet werden: {self._error}"
        else:
            text = recognition_text(self._recognition)
            if self._recognition.get("state") == "not_evaluated" and any(
                getattr(item, "quality", "legacy") == "legacy" for item in self._suggestions
            ):
                text = "Diese Vorschläge stammen aus einem älteren Datenstand. Mit „Neu bewerten“ aktualisieren."
        self.status_label.setToolTip(recognition_text(self._recognition))
        if self._offline:
            text = (
                "Offline – gespeicherter Datenstand; Entscheidungen benötigen eine Verbindung. "
                + text
            )
        if not self._suggestions and not self._error and not self._busy:
            text += " Keine offenen Entscheidungen in diesem Datenstand."
        self.status_label.setText(text)

    def _conflict(self, suggestion) -> str:
        if getattr(suggestion, "suggestion_type", "field") == "contact":
            text = self._contact_conflict_text(getattr(suggestion, "contact", None))
            if text:
                return text
        if self._customer is not None:
            payload = getattr(suggestion, "payload", {}) or {}
            proposed = (
                payload
                if getattr(suggestion, "suggestion_type", "field") == "address"
                else {suggestion.field_name: suggestion.value}
            )
            conflicts = []
            for field, new in proposed.items():
                current = getattr(self._customer, field, "")
                if (
                    current
                    and new
                    and str(current).strip().casefold() != str(new).strip().casefold()
                ):
                    conflicts.append(
                        f"{FIELD_LABELS.get(field, field)}: aktuell „{current}“, vorgeschlagen „{new}“"
                    )
            if conflicts:
                return "Abweichung von Kundendaten: " + "; ".join(conflicts)
        return (
            "Vorhandene Angaben widersprechen diesem Vorschlag."
            if getattr(suggestion, "is_conflict", False)
            else ""
        )

    def _category(self, suggestion) -> str:
        if self._conflict(suggestion):
            return "Konflikte"
        return (
            "Weitere Kontakte"
            if getattr(suggestion, "suggestion_type", "field") == "contact"
            else "Ergänzungen"
        )

    def _render(self) -> None:
        content = QWidget()
        content.setObjectName("RecognitionReviewContent")
        content.setMinimumWidth(0)
        content.setAutoFillBackground(False)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)
        groups = defaultdict(list)
        for suggestion in self._suggestions[: self.PAGE_SIZE]:
            groups[self._category(suggestion)].append(suggestion)
        visible = 0
        hidden = 0
        for category in ("Ergänzungen", "Konflikte", "Weitere Kontakte"):
            if not groups[category]:
                continue
            layout.addWidget(self._label(category, "ReviewGroupTitle"))
            field_counts = defaultdict(int)
            for suggestion in groups[category]:
                key = (getattr(suggestion, "suggestion_type", "field"), suggestion.field_name)
                field_counts[key] += 1
                if field_counts[key] > self._visible_per_field:
                    hidden += 1
                    continue
                layout.addWidget(self._card(suggestion))
                visible += 1
        layout.addStretch(1)
        old = self.scroll.takeWidget()
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self.scroll.setWidget(content)
        self._hidden_count = hidden
        self.more_button.setVisible(
            bool(hidden or self._has_more or len(self._suggestions) > self.PAGE_SIZE)
        )
        self.previous_button.setVisible(self._offset > 0)
        self.page_label.setText(f"{self._total} offene Entscheidungen · {visible} angezeigt")
        self.set_busy(self._busy)

    def _show_more(self) -> None:
        if self._busy:
            return
        if self._hidden_count:
            self._visible_per_field += 3
            self._render()
        else:
            self._request_page(self._offset + min(len(self._suggestions), self.PAGE_SIZE))

    def _request_page(self, offset: int) -> None:
        if not self._busy:
            self.set_busy(True)
            self.pageRequested.emit(offset)

    def _card(self, suggestion) -> QWidget:
        card = QFrame()
        card.setObjectName("CustomerSuggestionCard")
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        contact = getattr(suggestion, "contact", None)
        kind = getattr(suggestion, "suggestion_type", "field")
        if kind == "contact" and contact:
            label, value = "Ansprechpartner", contact.name or "Unbenannter Kontakt"
        elif kind == "address":
            payload = getattr(suggestion, "payload", {}) or {}
            label = "Anschrift"
            value = " · ".join(str(payload.get(key, "")) for key in ("street", "postal_code", "city") if payload.get(key))
        else:
            label, value = FIELD_LABELS.get(suggestion.field_name, suggestion.field_name), suggestion.value
        layout.addWidget(self._label(label, "ReviewField"))
        layout.addWidget(self._label(value, "ReviewValue"))
        if kind == "contact" and contact:
            details = " · ".join(f"{label}: {item}" for label, item in (
                ("Rolle", contact.role), ("E-Mail", contact.email), ("Telefon", contact.phone)
            ) if item)
            if details:
                layout.addWidget(self._label(details, "ReviewCurrent"))
        elif self._customer is not None:
            fields = ("street", "postal_code", "city") if kind == "address" else (suggestion.field_name,)
            current = " · ".join(str(getattr(self._customer, field, "")) for field in fields if getattr(self._customer, field, ""))
            layout.addWidget(self._label("Bisher: " + (current or "nicht hinterlegt"), "ReviewCurrent"))
        conflict = self._conflict(suggestion)
        incomplete_address = kind == "address" and not all(
            suggestion.payload.get(key) for key in ("street", "postal_code", "city")
        )
        if incomplete_address:
            layout.addWidget(self._label("Anschrift unvollständig. Bitte manuell ergänzen.", "ReviewWarning"))
        if conflict:
            layout.addWidget(self._label(conflict + " Bitte manuell auflösen.", "ReviewWarning"))
        count = getattr(suggestion, "evidence_count", 1)
        layout.addWidget(self._label(f"{quality_label(suggestion)} · {count} Beleg(e)", "ReviewMeta"))
        evidence = getattr(suggestion, "evidence", ()) or (
            {"source": suggestion.source.to_dict(), "excerpt": suggestion.excerpt},
        )
        evidence_box = QWidget()
        evidence_box.setObjectName("ReviewEvidencePanel")
        evidence_layout = QVBoxLayout(evidence_box)
        evidence_layout.setContentsMargins(0, 8, 0, 0)
        evidence_layout.setSpacing(8)
        role = getattr(suggestion, "party_role", "unknown")
        reasons = getattr(suggestion, "reasons", ()) or ((suggestion.rule,) if suggestion.rule else ())
        explanation = "Erkannte Rolle: " + {"customer": "Kunde", "contact": "Ansprechpartner", "unknown": "Unklar"}.get(role, role)
        if reasons:
            explanation += " · " + "; ".join(dict.fromkeys(reason_label(reason) for reason in reasons))
        evidence_layout.addWidget(self._label(explanation, "ReviewMeta"))
        sources = QWidget()
        sources_layout = QVBoxLayout(sources)
        sources_layout.setContentsMargins(0, 0, 0, 0)
        sources_layout.setSpacing(8)
        self._show_evidence(sources_layout, evidence, 0)
        evidence_layout.addWidget(sources)
        actions = QWidget()
        actions.setObjectName("ReviewCardActions")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 4, 0, 0)
        action_layout.setSpacing(8)
        toggle = AppButton("Belege anzeigen", AppButton.SECONDARY)
        toggle.setObjectName("ReviewEvidenceToggle")
        toggle.setCheckable(True)
        expanded = suggestion.id in self._expanded_evidence
        toggle.setChecked(expanded)
        evidence_box.setVisible(expanded)
        toggle.setText("Belege ausblenden" if expanded else "Belege anzeigen")
        toggle.toggled.connect(
            lambda checked, key=suggestion.id, panel=evidence_box, button=toggle:
            self._toggle_evidence(key, checked, panel, button)
        )
        action_layout.addWidget(toggle)
        reason = QComboBox()
        reason.setObjectName("ReviewRejectionReason")
        reason.setToolTip("Optionalen Grund für eine Ablehnung auswählen")
        reason.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        reason.setMinimumContentsLength(8)
        reason.setMinimumWidth(0)
        reason.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for text, reason_value in (
            ("Grund (optional)", ""), ("Kein gültiger Wert", "not_a_value"),
            ("Gehört zu jemand anderem", "wrong_customer"), ("Veraltet", "outdated"),
            ("Bereits vorhanden", "already_present"), ("Anderer Grund", "other"),
        ):
            reason.addItem(text, reason_value)
        reason.setCurrentIndex(max(0, reason.findData(self._rejection_reasons.get(suggestion.id, ""))))
        reason.currentIndexChanged.connect(
            lambda _index, key=suggestion.id, box=reason:
            self._rejection_reasons.__setitem__(key, box.currentData())
        )
        reason.setEnabled(not self._offline)
        action_layout.addWidget(reason, 1)
        reject = AppButton("Ablehnen", AppButton.SECONDARY)
        reject.setObjectName("ReviewRejectButton")
        reject.setEnabled(not self._offline)
        reject.clicked.connect(lambda _checked=False, value=suggestion: self._decide(value, "reject"))
        manual = bool(conflict) or incomplete_address
        accept = AppButton("Manuell bearbeiten" if manual else "Annehmen")
        accept.setObjectName("ReviewAcceptButton")
        accept.setEnabled(not self._offline)
        if manual:
            accept.clicked.connect(self.manualResolutionRequested.emit)
        else:
            accept.clicked.connect(lambda _checked=False, value=suggestion: self._decide(value, "accept"))
        action_layout.addWidget(reject)
        action_layout.addWidget(accept)
        layout.addWidget(actions)
        layout.addWidget(evidence_box)
        return card

    def _toggle_evidence(self, identifier, expanded, panel, button):
        if expanded:
            self._expanded_evidence.add(identifier)
        else:
            self._expanded_evidence.discard(identifier)
        panel.setVisible(expanded)
        button.setText("Belege ausblenden" if expanded else "Belege anzeigen")

    def _show_evidence(self, layout, evidence, offset) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for item in evidence[offset : offset + 3]:
            try:
                source = item.get("source")
                source = source if isinstance(source, SourcePath) else SourcePath.from_dict(source)
            except (TypeError, ValueError, AttributeError):
                continue
            source_card = QFrame()
            source_card.setObjectName("ReviewSourceCard")
            source_layout = QVBoxLayout(source_card)
            source_layout.setContentsMargins(10, 8, 10, 8)
            source_layout.setSpacing(6)
            locator = item.get("locator", {}) or item
            location = " · ".join(f"{label} {locator[key]}" for key, label in (
                ("page", "Seite"), ("sheet", "Blatt"), ("cell", "Zelle"), ("block_id", "Abschnitt"),
            ) if locator.get(key))
            path = PurePosixPath(source.relative_path)
            button = ReviewSourceButton("Quelle: " + path.name, source.relative_path)
            button.clicked.connect(lambda _checked=False, value=source: self.sourceRequested.emit(value))
            source_layout.addWidget(button)
            if location:
                source_layout.addWidget(self._label(location, "ReviewMeta"))
            # Full path is available without occupying the entire card; copying
            # or opening the source always uses the unchanged SourcePath object.
            context = self._label(str(path.parent), "ReviewSourcePath")
            context.setToolTip(source.relative_path)
            source_layout.addWidget(context)
            if item.get("excerpt"):
                excerpt = self._label(str(item["excerpt"]).strip(), "ReviewExcerpt")
                source_layout.addWidget(excerpt)
            layout.addWidget(source_card)
        if len(evidence) > 3:
            button = AppButton(f"Weitere Belege ({len(evidence)})", AppButton.SECONDARY)
            next_offset = offset + 3 if offset + 3 < len(evidence) else 0
            button.clicked.connect(lambda: self._show_evidence(layout, evidence, next_offset))
            layout.addWidget(button)

    def _contact_conflict_text(self, proposed: Contact | None) -> str:
        if self._customer is None or proposed is None:
            return ""
        for existing in self._customer.contacts:
            same_id = bool(
                getattr(proposed, "id", None) and proposed.id == getattr(existing, "id", None)
            )
            same_name = bool(
                proposed.name.strip()
                and existing.name.strip().casefold() == proposed.name.strip().casefold()
            )
            same_email = bool(
                proposed.email.strip()
                and existing.email.strip().casefold() == proposed.email.strip().casefold()
            )
            if not (same_id or same_name or same_email):
                continue
            conflicts = []
            for label, current, new in (
                ("Rolle", existing.role, proposed.role),
                ("E-Mail", existing.email, proposed.email),
                ("Telefon", existing.phone, proposed.phone),
            ):
                if (
                    current.strip()
                    and new.strip()
                    and current.strip().casefold() != new.strip().casefold()
                ):
                    conflicts.append(f"{label}: „{current.strip()}“ statt „{new.strip()}“")
            if conflicts:
                return (
                    "Abweichung von manuell gepflegten Kontaktdaten; vorhandene Angaben bleiben erhalten: "
                    + "; ".join(conflicts)
                )
        return ""

    def _decide(self, suggestion, action: str) -> None:
        if self._busy or self._offline or (action == "accept" and self._conflict(suggestion)):
            return
        self.set_busy(True)
        self.decisionRequested.emit(suggestion, action, self._revision)
