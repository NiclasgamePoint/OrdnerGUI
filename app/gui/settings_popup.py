from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from app.gui.widgets import AppButton, BusyIndicator


class SettingsPopup(QFrame):
    """Centered settings popup with navigation and content panels."""

    appearanceChanged = Signal(str, str)
    dataPathChanged = Signal(str)
    reindexRequested = Signal()

    def __init__(
        self,
        mode: str,
        accent: str,
        parent=None,
        data_path=None,
        indexing: bool = False,
    ):
        super().__init__(
            parent,
            Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint,
        )
        self.setObjectName("SettingsPopup")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(520, 360)

        self.selected_mode = mode if mode in {"light", "dark"} else "light"
        self.selected_accent = QColor(accent).name() if QColor(accent).isValid() else "#2db89d"
        self.data_path = str(data_path or "")
        self.indexing = indexing

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        content = QFrame()
        content.setObjectName("SettingsBody")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 16, 18, 14)
        content_layout.setSpacing(14)

        title = QLabel("Settings")
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

        appearance_item = QListWidgetItem("Aussehen")
        appearance_item.setSizeHint(QSize(164, 42))
        self.nav_list.addItem(appearance_item)

        self.nav_list.setCurrentRow(0)
        split_layout.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        split_layout.addWidget(self.stack, 1)

        self.stack.addWidget(self._build_general_page())
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
            "Erstellt Metadaten und durchsuchbare Inhalte für PDF-, Word- und Excel-Dateien neu."
        )
        index_hint.setObjectName("PopupCaption")
        index_hint.setWordWrap(True)
        layout.addWidget(index_hint)

        reindex_row = QHBoxLayout()
        self.reindex_button = AppButton("Index neu aufbauen", AppButton.SECONDARY)
        self.reindex_button.clicked.connect(self.request_reindex)
        reindex_row.addWidget(self.reindex_button)
        self.index_busy_indicator = BusyIndicator()
        reindex_row.addWidget(self.index_busy_indicator)
        reindex_row.addStretch()
        layout.addLayout(reindex_row)

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
            return QSize(700, 420)

        available_width = max(520, parent.width() - 48)
        available_height = max(360, parent.height() - 48)
        return QSize(min(720, available_width), min(460, available_height))
