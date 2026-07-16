from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QFormLayout,
    QCheckBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from app.gui.widgets import AppButton, BusyIndicator
from app.core.config import CustomerRecognitionOptions, IndexOptions
from app.core.index_diagnostics import IndexDiagnostics


class SettingsPopup(QFrame):
    """Centered settings popup with navigation and content panels."""

    appearanceChanged = Signal(str, str)
    dataPathChanged = Signal(str)
    reindexRequested = Signal()
    cancelIndexRequested = Signal()
    loadBackupRequested = Signal(str)
    indexOptionsChanged = Signal(object)
    customerRecognitionOptionsChanged = Signal(object)
    reviewRecognitionRequested = Signal()

    def __init__(
        self,
        mode: str,
        accent: str,
        parent=None,
        data_path=None,
        indexing: bool = False,
        backups=None,
        index_options: IndexOptions | None = None,
        diagnostics: IndexDiagnostics | None = None,
        recognition_options: CustomerRecognitionOptions | None = None,
        recognition_summary: dict | None = None,
        pending_recognition_cases: int = 0,
    ):
        super().__init__(
            parent,
            Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint,
        )
        self.setObjectName("SettingsPopup")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(640, 480)

        self.selected_mode = mode if mode in {"light", "dark"} else "light"
        self.selected_accent = QColor(accent).name() if QColor(accent).isValid() else "#2db89d"
        self.data_path = str(data_path or "")
        self.indexing = indexing
        self.backups = list(backups or [])
        self.index_options = index_options or IndexOptions()
        self.diagnostics = diagnostics
        self.recognition_options = recognition_options or CustomerRecognitionOptions()
        self.recognition_summary = dict(recognition_summary or {})
        self.pending_recognition_cases = int(pending_recognition_cases)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        content = QFrame()
        content.setObjectName("SettingsBody")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 16, 18, 14)
        content_layout.setSpacing(14)

        title = QLabel("Einstellungen")
        title.setObjectName("PopupTitle")
        content_layout.addWidget(title)

        split_layout = QHBoxLayout()
        split_layout.setSpacing(16)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("SettingsNav")
        self.nav_list.setFixedWidth(184)
        self.nav_list.setSpacing(2)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        general_item = QListWidgetItem("Allgemein")
        general_item.setSizeHint(QSize(164, 42))
        self.nav_list.addItem(general_item)

        index_item = QListWidgetItem("Indexierung")
        index_item.setSizeHint(QSize(164, 42))
        self.nav_list.addItem(index_item)

        recognition_item = QListWidgetItem("Kundenerkennung")
        recognition_item.setSizeHint(QSize(164, 42))
        self.nav_list.addItem(recognition_item)

        diagnostics_item = QListWidgetItem("Diagnose")
        diagnostics_item.setSizeHint(QSize(164, 42))
        self.nav_list.addItem(diagnostics_item)

        appearance_item = QListWidgetItem("Aussehen")
        appearance_item.setSizeHint(QSize(164, 42))
        self.nav_list.addItem(appearance_item)

        self.nav_list.setCurrentRow(0)
        split_layout.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        split_layout.addWidget(self.stack, 1)

        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_index_page())
        self.stack.addWidget(self._build_recognition_page())
        self.stack.addWidget(self._build_diagnostics_page())
        self.stack.addWidget(self._build_appearance_page())

        content_layout.addLayout(split_layout)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = AppButton("Schließen", AppButton.SECONDARY, minimum_width=96)
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        content_layout.addLayout(close_row)

        root_layout.addWidget(content)

        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)

    def _build_general_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("SettingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        heading = QLabel("Allgemein")
        heading.setObjectName("PopupSectionTitle")
        layout.addWidget(heading)

        path_label = QLabel("Datenquelle")
        path_label.setObjectName("PopupCaption")
        layout.addWidget(path_label)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.data_path_input = QLineEdit(self.data_path)
        self.data_path_input.setPlaceholderText("Ordner mit den zu durchsuchenden Daten")
        self.data_path_input.setCursorPosition(0)
        self.data_path_input.textChanged.connect(self._clear_path_error)
        path_row.addWidget(self.data_path_input, 1)

        browse_button = AppButton("Durchsuchen", AppButton.SECONDARY)
        browse_button.clicked.connect(self.choose_data_path)
        path_row.addWidget(browse_button)
        layout.addLayout(path_row)

        hint = QLabel(
            "Beim Übernehmen wird der lokale Index für diese Datenquelle neu aufgebaut."
        )
        hint.setObjectName("PopupCaption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.path_error_label = QLabel("")
        self.path_error_label.setObjectName("SettingsError")
        self.path_error_label.setWordWrap(True)
        layout.addWidget(self.path_error_label)

        apply_row = QHBoxLayout()
        apply_row.addStretch()
        apply_button = AppButton("Pfad übernehmen")
        apply_button.clicked.connect(self.apply_data_path)
        apply_row.addWidget(apply_button)
        layout.addLayout(apply_row)

        index_label = QLabel("Suchindex")
        index_label.setObjectName("PopupCaption")
        layout.addWidget(index_label)

        index_hint = QLabel(
            "Verarbeitet nur neue oder geänderte Dateien. Der Lauf wird nach dem Schließen "
            "des Programms im Hintergrund fortgesetzt."
        )
        index_hint.setObjectName("PopupCaption")
        index_hint.setWordWrap(True)
        layout.addWidget(index_hint)

        reindex_row = QHBoxLayout()
        self.reindex_button = AppButton("Index aktualisieren", AppButton.SECONDARY)
        self.reindex_button.clicked.connect(self.request_reindex)
        reindex_row.addWidget(self.reindex_button)
        self.index_busy_indicator = BusyIndicator()
        reindex_row.addWidget(self.index_busy_indicator)
        self.cancel_index_button = AppButton("Abbrechen", AppButton.DANGER)
        self.cancel_index_button.clicked.connect(self.cancelIndexRequested.emit)
        reindex_row.addWidget(self.cancel_index_button)
        reindex_row.addStretch()
        layout.addLayout(reindex_row)

        self.index_progress_label = QLabel("")
        self.index_progress_label.setObjectName("PopupCaption")
        self.index_progress_label.setWordWrap(True)
        layout.addWidget(self.index_progress_label)

        backup_label = QLabel("Alte Indexstände")
        backup_label.setObjectName("PopupCaption")
        layout.addWidget(backup_label)

        backup_row = QHBoxLayout()
        self.backup_combo = QComboBox()
        backup_row.addWidget(self.backup_combo, 1)
        self.load_backup_button = AppButton("Index laden", AppButton.SECONDARY)
        self.load_backup_button.clicked.connect(self.load_selected_backup)
        backup_row.addWidget(self.load_backup_button)
        layout.addLayout(backup_row)
        self.set_backups(self.backups)

        layout.addStretch()
        self.set_indexing(self.indexing)
        return page

    def request_reindex(self):
        if self.indexing:
            return
        self.set_indexing(True)
        self.reindexRequested.emit()

    def set_indexing(self, indexing: bool):
        self.indexing = indexing
        if not hasattr(self, "reindex_button"):
            return
        self.reindex_button.set_busy(indexing)
        if indexing:
            self.index_busy_indicator.start()
        else:
            self.index_busy_indicator.stop()
            self.index_progress_label.clear()
        self.cancel_index_button.setVisible(indexing)
        self.cancel_index_button.setEnabled(indexing)
        self.load_backup_button.setEnabled(not indexing and bool(self.backups))

    def set_index_progress(self, processed_count: int, filename: str):
        self.index_progress_label.setText(
            f"{processed_count} Dateien geprüft · {filename}"
        )

    def set_backups(self, backups):
        self.backups = list(backups or [])
        if not hasattr(self, "backup_combo"):
            return
        self.backup_combo.clear()
        for backup in self.backups:
            self.backup_combo.addItem(backup["label"], backup["path"])
        if not self.backups:
            self.backup_combo.addItem("Keine Sicherung vorhanden", "")
        self.load_backup_button.setEnabled(bool(self.backups) and not self.indexing)

    def load_selected_backup(self):
        backup_path = self.backup_combo.currentData()
        if backup_path:
            self.loadBackupRequested.emit(str(backup_path))

    def _build_index_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("SettingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(12)

        heading = QLabel("Indexierung")
        heading.setObjectName("PopupSectionTitle")
        layout.addWidget(heading)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        self.max_file_size_spin = QSpinBox()
        self.max_file_size_spin.setRange(1, 10_240)
        self.max_file_size_spin.setSuffix(" MB")
        self.max_file_size_spin.setValue(self.index_options.max_file_size_mb)
        form.addRow(self._form_label("Maximale Dokumentgröße"), self.max_file_size_spin)

        self.max_characters_spin = QSpinBox()
        self.max_characters_spin.setRange(10_000, 20_000_000)
        self.max_characters_spin.setSingleStep(100_000)
        self.max_characters_spin.setValue(self.index_options.max_extracted_characters)
        form.addRow(self._form_label("Maximale Extraktlänge"), self.max_characters_spin)

        self.result_limit_spin = QSpinBox()
        self.result_limit_spin.setRange(10, 5_000)
        self.result_limit_spin.setValue(self.index_options.result_limit)
        form.addRow(self._form_label("Maximale Suchtreffer"), self.result_limit_spin)

        self.ocr_checkbox = QCheckBox("OCR für gescannte PDFs verwenden")
        self.ocr_checkbox.setChecked(self.index_options.ocr_enabled)
        form.addRow(self._form_label("OCR"), self.ocr_checkbox)

        self.ocr_pages_spin = QSpinBox()
        self.ocr_pages_spin.setRange(1, 1_000)
        self.ocr_pages_spin.setValue(self.index_options.ocr_max_pages)
        form.addRow(self._form_label("Maximale OCR-Seiten"), self.ocr_pages_spin)

        self.ocr_timeout_spin = QSpinBox()
        self.ocr_timeout_spin.setRange(5, 600)
        self.ocr_timeout_spin.setSuffix(" s")
        self.ocr_timeout_spin.setValue(self.index_options.ocr_timeout_seconds)
        form.addRow(self._form_label("OCR-Zeitlimit pro PDF"), self.ocr_timeout_spin)

        self.content_extensions_input = QLineEdit(self.index_options.content_extensions)
        self.content_extensions_input.setPlaceholderText("pdf, docx, xlsx, txt, …")
        form.addRow(self._form_label("Durchsuchbare Formate"), self.content_extensions_input)

        self.excluded_folders_input = QLineEdit(self.index_options.excluded_folders)
        self.excluded_folders_input.setPlaceholderText(".git, .venv, node_modules")
        form.addRow(self._form_label("Ausgeschlossene Ordner"), self.excluded_folders_input)
        layout.addLayout(form)

        note = QLabel(
            "Änderungen an Extraktion oder Ausschlüssen werden beim nächsten Indexlauf angewendet."
        )
        note.setObjectName("PopupCaption")
        note.setWordWrap(True)
        layout.addWidget(note)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_button = AppButton("Indexeinstellungen speichern")
        save_button.clicked.connect(self.save_index_options)
        save_row.addWidget(save_button)
        layout.addLayout(save_row)
        layout.addStretch()
        return page

    def _build_diagnostics_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("SettingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        heading = QLabel("Index-Diagnose")
        heading.setObjectName("PopupSectionTitle")
        layout.addWidget(heading)

        self.diagnostics_text = QPlainTextEdit()
        self.diagnostics_text.setObjectName("DiagnosticsText")
        self.diagnostics_text.setReadOnly(True)
        layout.addWidget(self.diagnostics_text, 1)
        self.set_diagnostics(self.diagnostics)
        return page

    def _build_recognition_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("SettingsPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        heading = QLabel("Automatische Kundenerkennung")
        heading.setObjectName("PopupSectionTitle")
        layout.addWidget(heading)

        self.recognition_enabled = QCheckBox(
            "Kunden ab 2016 aus Dienstleistung/Jahr/Nachname, Ort erkennen"
        )
        self.recognition_enabled.setChecked(self.recognition_options.enabled)
        layout.addWidget(self.recognition_enabled)

        note = QLabel(
            "Manuell gepflegte Daten werden nicht überschrieben. Die folgenden Werte "
            "werden nur aus automatisch erkannten Kontaktdaten entfernt."
        )
        note.setObjectName("PopupCaption")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.recognition_blacklist_fields: dict[str, QPlainTextEdit] = {}
        blacklist_specs = [
            ("email_blacklist", "E-Mail-Adressen", "eine Adresse pro Zeile"),
            ("phone_blacklist", "Telefonnummern", "eine Nummer pro Zeile"),
            ("name_blacklist", "Namen", "ein Name pro Zeile"),
            ("address_blacklist", "Adressen", "eine Adresse pro Zeile"),
            ("text_blacklist", "Beliebige Textwerte", "ein Textfragment pro Zeile"),
        ]
        for attribute, label_text, placeholder in blacklist_specs:
            label = QLabel(label_text)
            label.setObjectName("PopupCaption")
            layout.addWidget(label)
            field = QPlainTextEdit()
            field.setFixedHeight(58)
            field.setPlaceholderText(placeholder)
            field.setPlainText(str(getattr(self.recognition_options, attribute)))
            self.recognition_blacklist_fields[attribute] = field
            layout.addWidget(field)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_button = AppButton("Kundenerkennung speichern")
        save_button.clicked.connect(self.save_customer_recognition_options)
        save_row.addWidget(save_button)
        layout.addLayout(save_row)

        status_title = QLabel("Letzter Erkennungslauf")
        status_title.setObjectName("PopupCaption")
        layout.addWidget(status_title)
        self.recognition_status = QLabel("")
        self.recognition_status.setWordWrap(True)
        layout.addWidget(self.recognition_status)
        self.review_recognition_button = AppButton(
            "Prüffälle öffnen", AppButton.SECONDARY
        )
        self.review_recognition_button.clicked.connect(
            self.reviewRecognitionRequested.emit
        )
        layout.addWidget(self.review_recognition_button)
        layout.addStretch()
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        self.set_recognition_state(
            self.recognition_summary, self.pending_recognition_cases
        )
        return page

    def save_customer_recognition_options(self):
        self.recognition_options = CustomerRecognitionOptions(
            enabled=self.recognition_enabled.isChecked(),
            minimum_year=2016,
            **{
                attribute: field.toPlainText().strip()
                for attribute, field in self.recognition_blacklist_fields.items()
            },
        )
        self.customerRecognitionOptionsChanged.emit(self.recognition_options)

    def set_recognition_state(self, summary: dict | None, pending: int):
        self.recognition_summary = dict(summary or {})
        self.pending_recognition_cases = int(pending)
        if not hasattr(self, "recognition_status"):
            return
        values = self.recognition_summary
        self.recognition_status.setText(
            f"{int(values.get('detected') or 0)} erkannt · "
            f"{int(values.get('created') or 0)} angelegt · "
            f"{int(values.get('assigned') or 0)} zugeordnet · "
            f"{int(values.get('skipped') or 0)} übersprungen · "
            f"{self.pending_recognition_cases} offen"
        )
        self.review_recognition_button.setText(
            f"Prüffälle öffnen ({self.pending_recognition_cases})"
        )
        self.review_recognition_button.setEnabled(self.pending_recognition_cases > 0)

    def set_diagnostics(self, diagnostics: IndexDiagnostics | None):
        self.diagnostics = diagnostics
        if not hasattr(self, "diagnostics_text"):
            return
        if diagnostics is None:
            self.diagnostics_text.setPlainText("Noch keine Diagnosedaten vorhanden.")
            return

        status_labels = {
            "success": "Erfolgreich",
            "empty": "Ohne extrahierbaren Text",
            "error": "Fehler",
            "timeout": "OCR-Zeitlimit erreicht",
            "encrypted": "Verschlüsselt/kennwortgeschützt",
            "skipped_large": "Wegen Größe übersprungen",
            "not_applicable": "Nur Metadaten",
        }
        lines = [
            f"Integrität: {diagnostics.integrity}",
            f"Quelle: {diagnostics.root_path or '-'}",
            f"Letzter Lauf: {diagnostics.built_at or '-'}",
            f"Modus: {diagnostics.build_mode or '-'}",
            f"Dauer: {diagnostics.duration_seconds:.2f} Sekunden",
            f"Geprüfte Dateien: {diagnostics.file_count}",
            f"Erkannte Ordner: {diagnostics.folder_count}",
            f"Änderungen: {diagnostics.changed_count}",
            f"Volltext-Extrakte: {diagnostics.content_count}",
            f"Indexgröße: {diagnostics.database_size / 1024 / 1024:.2f} MB",
            "",
            "Extraktionsstatus:",
        ]
        for status, count in sorted(diagnostics.status_counts.items()):
            lines.append(f"  {status_labels.get(status, status)}: {count}")
        if diagnostics.errors:
            lines.extend(["", "Extraktionshinweise:"])
            for entry in diagnostics.errors:
                lines.append(f"  {entry['path']}: {entry['error']}")
        self.diagnostics_text.setPlainText("\n".join(lines))

    def _form_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("PopupCaption")
        return label

    def save_index_options(self):
        self.index_options = IndexOptions(
            max_file_size_mb=self.max_file_size_spin.value(),
            max_extracted_characters=self.max_characters_spin.value(),
            result_limit=self.result_limit_spin.value(),
            ocr_enabled=self.ocr_checkbox.isChecked(),
            ocr_max_pages=self.ocr_pages_spin.value(),
            ocr_timeout_seconds=self.ocr_timeout_spin.value(),
            content_extensions=self.content_extensions_input.text().strip(),
            excluded_folders=self.excluded_folders_input.text().strip(),
        )
        self.indexOptionsChanged.emit(self.index_options)

    def choose_data_path(self):
        start_path = self.data_path_input.text().strip() or self.data_path
        popup_position = self.pos()

        # A native file dialog takes focus away from a Qt.Popup. Temporarily
        # keep this widget alive so it can be restored after the dialog closes.
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        try:
            selected_path = QFileDialog.getExistingDirectory(
                self.parentWidget(),
                "Datenquelle auswählen",
                start_path,
                QFileDialog.ShowDirsOnly,
            )
        finally:
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.move(popup_position)
            self.show()
            self.raise_()
            self.activateWindow()

        if selected_path:
            self.data_path_input.setText(selected_path)
            self.data_path_input.setCursorPosition(0)

    def apply_data_path(self):
        from pathlib import Path

        raw_path = self.data_path_input.text().strip()
        if not raw_path:
            self.path_error_label.setText("Bitte einen Datenordner auswählen.")
            return

        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_dir():
            self.path_error_label.setText("Der ausgewählte Datenordner existiert nicht.")
            return

        resolved_path = path.resolve()
        self.data_path = str(resolved_path)
        self.data_path_input.setText(self.data_path)
        self.dataPathChanged.emit(self.data_path)
        self.close()

    def _clear_path_error(self):
        self.path_error_label.setText("")

    def _build_appearance_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("SettingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        heading = QLabel("Aussehen")
        heading.setObjectName("PopupSectionTitle")
        layout.addWidget(heading)

        mode_row = QHBoxLayout()
        mode_label = QLabel("Modus")
        mode_label.setObjectName("PopupCaption")
        mode_row.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Light", "light")
        self.mode_combo.addItem("Dark", "dark")
        self.mode_combo.setCurrentIndex(0 if self.selected_mode == "light" else 1)
        mode_row.addWidget(self.mode_combo)
        layout.addLayout(mode_row)

        accent_row = QHBoxLayout()
        accent_label = QLabel("Akzent")
        accent_label.setObjectName("PopupCaption")
        accent_row.addWidget(accent_label)

        self.accent_combo = QComboBox()
        self.accent_combo.addItem("Mint", "#2db89d")
        self.accent_combo.addItem("Ocean", "#1f7dbf")
        self.accent_combo.addItem("Sunset", "#f08a3c")
        self.accent_combo.addItem("Berry", "#c1457b")
        self.accent_combo.addItem("Forest", "#2f9b61")
        self.accent_combo.addItem("Custom", "custom")
        accent_row.addWidget(self.accent_combo)
        layout.addLayout(accent_row)

        chooser_row = QHBoxLayout()
        self.preview = QFrame()
        self.preview.setObjectName("AccentPreview")
        self.preview.setFixedSize(44, 26)
        chooser_row.addWidget(self.preview)

        self.pick_button = AppButton("Farbe wählen", AppButton.SECONDARY)
        self.pick_button.clicked.connect(self.pick_custom_color)
        chooser_row.addWidget(self.pick_button)
        chooser_row.addStretch()
        layout.addLayout(chooser_row)

        layout.addStretch()

        self.mode_combo.currentIndexChanged.connect(self.on_value_changed)
        self.accent_combo.currentIndexChanged.connect(self.on_value_changed)

        self._set_combo_for_accent(self.selected_accent)
        self._update_preview()
        self.pick_button.setEnabled(self.accent_combo.currentData() == "custom")

        return page

    def _set_combo_for_accent(self, accent: str):
        normalized = QColor(accent).name() if QColor(accent).isValid() else "#2db89d"
        found = False
        for idx in range(self.accent_combo.count() - 1):
            if self.accent_combo.itemData(idx) == normalized:
                self.accent_combo.setCurrentIndex(idx)
                found = True
                break
        if not found:
            self.accent_combo.setCurrentIndex(self.accent_combo.count() - 1)
        self.selected_accent = normalized

    def _update_preview(self):
        self.preview.setStyleSheet(
            f"background-color: {self.selected_accent}; border: 1px solid #91a0a9; border-radius: 6px;"
        )

    def on_value_changed(self):
        self.selected_mode = self.mode_combo.currentData()
        accent_value = self.accent_combo.currentData()
        if accent_value and accent_value != "custom":
            self.selected_accent = accent_value
        self.pick_button.setEnabled(self.accent_combo.currentData() == "custom")
        self._update_preview()
        self.appearanceChanged.emit(self.selected_mode, self.selected_accent)

    def pick_custom_color(self):
        color = QColorDialog.getColor(QColor(self.selected_accent), self, "Akzentfarbe wählen")
        if color.isValid():
            self.selected_accent = color.name()
            self.accent_combo.setCurrentIndex(self.accent_combo.count() - 1)
            self._update_preview()
            self.appearanceChanged.emit(self.selected_mode, self.selected_accent)

    def size_for_parent(self) -> QSize:
        """Return a comfortable size that still fits into a smaller main window."""
        parent = self.parentWidget()
        if parent is None:
            return QSize(780, 560)

        available_width = max(640, parent.width() - 48)
        available_height = max(480, parent.height() - 48)
        return QSize(min(820, available_width), min(600, available_height))
