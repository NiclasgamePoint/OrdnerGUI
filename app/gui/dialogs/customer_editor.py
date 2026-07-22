from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox, QCompleter, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QHeaderView,
    QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.core.config import DB_FILE
from app.core.customer_models import Contact, Customer
from app.core.index_manager import IndexManager
from app.core.customer_repository import CustomerRepository
from app.core.folder_structure import MINIMUM_CUSTOMER_YEAR
from app.core.search_models import SearchFilters
from app.gui.dialogs.centered_popup import CenteredPopupDialog
from app.gui.widgets.buttons import AppButton
from app.services.customer_suggestion import CustomerSuggestionService


@dataclass(frozen=True)
class FolderDisplayInfo:
    path: str
    name: str
    service: str = ""
    year: str = ""

    @property
    def context(self) -> str:
        return " · ".join(value for value in (self.service, self.year) if value)

    @property
    def summary(self) -> str:
        return f"{self.name} ({self.context})" if self.context else self.name


class CustomerEditorDialog(CenteredPopupDialog):
    AUTO_FILL_STYLE = "border: 1px solid #d7a832; background-color: rgba(215, 168, 50, 0.12);"
    SUGGESTED_ITEM_TOOLTIP = "Automatisch gefundener möglicher Kundenordner"

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
        self._suggested_folder_paths: set[str] = set()
        self._folder_display_cache: dict[str, FolderDisplayInfo] = {}
        self.existing_customer_combo: QComboBox | None = None
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
        self.setMinimumSize(700, 560)
        self.resize(820, 640)

        # Legacy saved data may have split folder names at commas.
        self.customer.folder_paths = [
            value
            for value in self._normalize_folder_values(self.customer.folder_paths)
            if self._is_supported_customer_folder(value)
        ]

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        body = QFrame()
        body.setObjectName("CustomerEditorBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        popup_title = QLabel("Kundendaten bearbeiten")
        popup_title.setObjectName("PopupSectionTitle")
        layout.addWidget(popup_title)

        linked_folder = folder_path or (self.customer.folder_paths[0] if self.customer.folder_paths else "-")
        linked_info = self._folder_display_info(linked_folder)
        title = QLabel(f"Verknüpfter Ordner: {linked_info.summary}")
        title.setObjectName("PopupCaption")
        title.setWordWrap(True)
        title.setToolTip(linked_folder)
        layout.addWidget(title)

        assignment_panel = self._build_existing_customer_assignment()
        if assignment_panel is not None:
            layout.addWidget(assignment_panel)

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
        root_layout.addWidget(body)

        if self.customer.id is None and self.context_folder_path:
            self._offer_auto_suggestions(suggested_name)

    def _build_existing_customer_assignment(self) -> QWidget | None:
        if self.customer.id is not None or not self.context_folder_path:
            return None

        customers = [
            customer
            for customer in self.repository.list_customers()
            if customer.id is not None
        ]
        if not customers:
            return None

        panel = QFrame()
        panel.setObjectName("ExistingCustomerAssignment")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(7)

        heading = QLabel("Ordner einem vorhandenen Kunden zuordnen")
        heading.setObjectName("PopupTitle")
        panel_layout.addWidget(heading)

        row = QHBoxLayout()
        row.setSpacing(8)
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setPlaceholderText("Kunden suchen und auswählen …")
        for customer in customers:
            context = " · ".join(
                value for value in (customer.city, customer.entity_type) if value
            )
            label = (
                f"{customer.display_name} · {context}"
                if context
                else customer.display_name
            )
            combo.addItem(label, customer.id)
        combo.setCurrentIndex(-1)
        completer = combo.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

        assign_button = AppButton("Ordner zuordnen")
        assign_button.clicked.connect(self._assign_to_existing_customer)
        row.addWidget(combo, 1)
        row.addWidget(assign_button)
        panel_layout.addLayout(row)

        hint = QLabel(
            "Alternativ können darunter neue Kundendaten für diesen Ordner angelegt werden."
        )
        hint.setObjectName("StatCaption")
        hint.setWordWrap(True)
        panel_layout.addWidget(hint)
        self.existing_customer_combo = combo
        return panel

    def _assign_to_existing_customer(self):
        combo = self.existing_customer_combo
        if combo is None:
            return

        current_index = combo.currentIndex()
        selected_id = (
            combo.itemData(current_index)
            if current_index >= 0
            and combo.currentText().strip() == combo.itemText(current_index)
            else None
        )
        if selected_id is None:
            QMessageBox.warning(
                self,
                "Ordner zuordnen",
                "Bitte einen vorhandenen Kunden aus der Liste auswählen.",
            )
            return
        if not self._is_supported_customer_folder(self.context_folder_path):
            QMessageBox.warning(
                self,
                "Ordner zuordnen",
                f"Ordner vor {MINIMUM_CUSTOMER_YEAR} werden nicht automatisch als Kundenordner berücksichtigt.",
            )
            return

        folder_info = self._folder_display_info(self.context_folder_path)
        try:
            self.customer = self.repository.add_folder_to_customer(
                int(selected_id),
                self.context_folder_path,
                folder_info.service,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Ordner zuordnen", str(error))
            return
        self.accept()

    def _build_master_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DialogPage")
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
        self.folder_paths = QLineEdit(
            " | ".join(self._folder_display_info(path).summary for path in folder_values)
        )
        self.folder_paths.setReadOnly(True)
        self.folder_paths.setPlaceholderText("Im Dateien-Tab auswählbar")
        self.folder_paths.setToolTip("\n".join(folder_values))
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
        page.setObjectName("DialogPage")
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
        page.setObjectName("DialogPage")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Notiz"))
        self.note_text = QPlainTextEdit()
        self.note_text.setPlaceholderText("Freitextnotiz zum Kunden …")
        self.note_text.setPlainText("\n\n".join(self.customer.notes))
        layout.addWidget(self.note_text, 1)
        return page

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DialogPage")
        layout = QVBoxLayout(page)

        hint = QLabel(
            "Ein Klick verschiebt Ordner zwischen Vorschlägen und Auswahl. "
            "Nur Einträge in 'Ausgewählte Ordner' werden gespeichert."
        )
        hint.setWordWrap(True)
        hint.setObjectName("StatCaption")
        layout.addWidget(hint)

        tables = QHBoxLayout()
        tables.setSpacing(10)

        found_panel = QVBoxLayout()
        found_panel.addWidget(QLabel("Gefundene mögliche Ordner"))
        self.found_folders_table = self._create_folder_table()
        self.found_folders_table.itemClicked.connect(lambda item: self._move_folder_item(item, self.found_folders_table, self.selected_folders_table, suggested=False))
        found_panel.addWidget(self.found_folders_table, 1)

        selected_panel = QVBoxLayout()
        selected_panel.addWidget(QLabel("Ausgewählte Ordner"))
        self.selected_folders_table = self._create_folder_table()
        self.selected_folders_table.itemClicked.connect(lambda item: self._move_folder_item(item, self.selected_folders_table, self.found_folders_table, suggested=True))
        selected_panel.addWidget(self.selected_folders_table, 1)

        tables.addLayout(selected_panel, 1)
        tables.addLayout(found_panel, 1)
        layout.addLayout(tables, 1)

        self._populate_folder_selection_tables()
        return page

    def _create_folder_table(self) -> QTableWidget:
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Ordner", "Dienstleistung", "Jahr"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _mark_auto_filled_widget(self, widget: QWidget):
        widget.setStyleSheet(self.AUTO_FILL_STYLE)
        widget.setToolTip("Automatisch aus Dokument-/Ordnerdaten vorausgefüllt")

    def _populate_folder_selection_tables(self):
        if self.customer.id is None:
            selected = []
        else:
            selected = self._normalize_folder_values(
                self.customer.folder_paths or ([self.customer.folder_path] if self.customer.folder_path else [])
            )
        selected = [path for path in selected if self._is_supported_customer_folder(path)]

        discovered = [
            path
            for path in self._discover_related_folders(self.customer.display_name)
            if self._is_supported_customer_folder(path)
        ]
        discovered_set = set(discovered)
        selected_set = set(selected)
        self._suggested_folder_paths = {path for path in discovered_set if path not in selected_set}

        self._fill_folder_table(self.selected_folders_table, selected, suggested=False)
        self._fill_folder_table(
            self.found_folders_table,
            [path for path in discovered if path not in selected_set],
            suggested=True,
        )
        self._sync_folder_line_edit_from_selection()

    def _fill_folder_table(self, table: QTableWidget, rows: list[str], suggested: bool):
        table.blockSignals(True)
        table.setRowCount(0)
        for path in rows:
            self._append_folder_row(table, path, suggested)
        table.blockSignals(False)

    def _append_folder_row(
        self,
        table: QTableWidget,
        path: str,
        suggested: bool,
    ):
        info = self._folder_display_info(path)
        row = table.rowCount()
        table.insertRow(row)
        tooltip = path
        if suggested:
            tooltip = f"{self.SUGGESTED_ITEM_TOOLTIP}\n{path}"
        for column, value in enumerate((info.name, info.service or "-", info.year or "-")):
            item = QTableWidgetItem(value)
            item.setData(Qt.UserRole, path)
            item.setToolTip(tooltip)
            if suggested:
                item.setBackground(self.palette().alternateBase())
            table.setItem(row, column, item)

    def _move_folder_item(
        self,
        item: QTableWidgetItem,
        source: QTableWidget,
        target: QTableWidget,
        suggested: bool,
    ):
        if item is None:
            return
        path = str(item.data(Qt.UserRole) or "").strip()
        if not path:
            return

        source_row = item.row()
        source.blockSignals(True)
        source.removeRow(source_row)
        source.blockSignals(False)

        if self._table_contains_path(target, path):
            self._sync_folder_line_edit_from_selection()
            return

        target.blockSignals(True)
        self._append_folder_row(target, path, suggested)
        target.blockSignals(False)
        self._sync_folder_line_edit_from_selection()

    def _table_contains_path(self, table: QTableWidget, path_value: str) -> bool:
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and str(item.data(Qt.UserRole) or "") == path_value:
                return True
        return False

    def _selected_folder_values(self) -> list[str]:
        values: list[str] = []
        for row in range(self.selected_folders_table.rowCount()):
            item = self.selected_folders_table.item(row, 0)
            path = str(item.data(Qt.UserRole) or "").strip() if item else ""
            if path and self._is_supported_customer_folder(path):
                values.append(path)
        return self._normalize_folder_values(values)

    def _extract_year_from_folder(self, path_value: str) -> int | None:
        for part in Path(path_value).parts:
            if re.fullmatch(r"(?:19|20)\d{2}", part):
                return int(part)
        return None

    def _is_supported_customer_folder(self, path_value: str) -> bool:
        year = self._extract_year_from_folder(path_value)
        return year is None or year >= MINIMUM_CUSTOMER_YEAR

    def _sync_folder_line_edit_from_selection(self):
        paths = self._selected_folder_values()
        self.folder_paths.setText(
            " | ".join(self._folder_display_info(path).summary for path in paths)
        )
        self.folder_paths.setToolTip("\n".join(paths))

    def _discover_related_folders(self, suggested_name: str) -> list[str]:
        queries: list[str] = []

        if self.context_folder_path:
            try:
                context_path = Path(self.context_folder_path).resolve()
            except OSError:
                context_path = Path(self.context_folder_path)
            if context_path.parent.name:
                queries.append(context_path.parent.name)
            if context_path.name:
                queries.append(context_path.name)

        if suggested_name.strip():
            queries.append(suggested_name.strip())

        company_name = self.customer.company.strip()
        if company_name:
            queries.append(company_name)

        normalized_queries: list[str] = []
        seen_queries: set[str] = set()
        for query in queries:
            cleaned = " ".join(query.split())
            if len(cleaned) < 2:
                continue
            key = cleaned.casefold()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            normalized_queries.append(cleaned)

        results: list[str] = []
        manager = None
        try:
            manager = IndexManager(DB_FILE, initialize=False)
            filters = SearchFilters()
            for query in normalized_queries:
                page = manager.search_folders_page(query, filters, page=1, page_size=200)
                for item in page.items:
                    path = item.get("folder_path", "")
                    if path:
                        self._folder_display_cache[path] = self._folder_display_info(
                            path,
                            str(item.get("relative_path") or ""),
                        )
                        results.append(path)
        except Exception:
            # If index search is unavailable, keep the dialog usable.
            results = []
        finally:
            if manager is not None:
                manager.close()

        return self._normalize_folder_values(results)

    def _folder_display_info(
        self,
        path_value: str,
        relative_path: str = "",
    ) -> FolderDisplayInfo:
        if not relative_path and path_value in self._folder_display_cache:
            return self._folder_display_cache[path_value]

        name = Path(path_value).name or path_value
        service = ""
        year = ""
        relative_parts = Path(relative_path).parts if relative_path else ()
        if relative_parts:
            service = relative_parts[0] if len(relative_parts) >= 1 else ""
            year = relative_parts[1] if len(relative_parts) >= 2 else ""
        else:
            path_parts = Path(path_value).parts
            bucket_index = next(
                (
                    index
                    for index, part in enumerate(path_parts)
                    if re.fullmatch(r"(?:19|20)\d{2}", part)
                    or part.casefold() == "vorlagen"
                ),
                None,
            )
            if bucket_index is not None:
                year = path_parts[bucket_index]
                if bucket_index > 0:
                    service = path_parts[bucket_index - 1]

        info = FolderDisplayInfo(path_value, name, service, year)
        self._folder_display_cache[path_value] = info
        return info

    def _normalize_lookup_token(self, text: str) -> str:
        token = (text or "").casefold().strip()
        for marker in [",", ";", ".", "-", "_", "(", ")", "[", "]"]:
            token = token.replace(marker, " ")
        return " ".join(token.split())

    def _parse_folder_values(self, raw_text: str) -> list[str]:
        text = (raw_text or "").strip()
        if not text:
            return []

        if "|" in text:
            values = [value.strip() for value in text.split("|") if value.strip()]
        else:
            # If there is no explicit separator, keep the full input as one path.
            values = [text]
        return self._normalize_folder_values(values)

    def _normalize_folder_values(self, values: list[str]) -> list[str]:
        merged: list[str] = []
        for value in values:
            token = (value or "").strip()
            if not token:
                continue

            # Heuristic to heal old data that accidentally split paths at commas,
            # e.g. ['/abs/path/abgeschlossene Messungen', 'Kaltenkirchen'].
            if merged and Path(merged[-1]).is_absolute() and not Path(token).is_absolute():
                merged[-1] = f"{merged[-1]}, {token}"
                continue

            merged.append(token)
        return merged

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
        folders = self._selected_folder_values()
        if self.context_folder_path and not folders:
            QMessageBox.warning(
                self,
                "Kundendaten",
                f"Bitte mindestens einen gueltigen Ordner ab {MINIMUM_CUSTOMER_YEAR} auswaehlen.",
            )
            return
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
