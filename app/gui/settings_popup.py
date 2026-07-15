from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class SettingsPopup(QFrame):
    """Centered settings popup with navigation and content panels."""

    appearanceChanged = Signal(str, str)

    def __init__(self, mode: str, accent: str, parent=None):
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
        close_button = QPushButton("Schließen")
        close_button.setObjectName("GhostButton")
        close_button.setMinimumWidth(96)
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

        heading = QLabel("Einstellungen")
        heading.setObjectName("PopupSectionTitle")
        layout.addWidget(heading)

        status = QLabel("in arbeit")
        status.setObjectName("PopupCaption")
        layout.addWidget(status)
        layout.addStretch()
        return page

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

        self.pick_button = QPushButton("Farbe wählen")
        self.pick_button.setObjectName("GhostButton")
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
