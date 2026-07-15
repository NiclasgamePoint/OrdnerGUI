from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ThemePopup(QFrame):
    """Lightweight popup attached to the settings button."""

    themeChanged = Signal(str, str)

    def __init__(self, mode: str, accent: str, parent: QWidget = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("ThemePopup")
        self.setMinimumWidth(280)

        self.selected_mode = mode if mode in {"light", "dark"} else "light"
        self.selected_accent = QColor(accent).name() if QColor(accent).isValid() else "#2db89d"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Design-Einstellungen")
        title.setObjectName("PopupTitle")
        layout.addWidget(title)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
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
        accent_row.setSpacing(8)
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

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        self.preview = QFrame()
        self.preview.setObjectName("AccentPreview")
        self.preview.setFixedSize(42, 26)
        bottom_row.addWidget(self.preview)

        self.pick_button = QPushButton("Farbe wählen")
        self.pick_button.setObjectName("GhostButton")
        self.pick_button.clicked.connect(self.pick_custom_color)
        bottom_row.addWidget(self.pick_button)
        layout.addLayout(bottom_row)

        self.mode_combo.currentIndexChanged.connect(self.on_value_changed)
        self.accent_combo.currentIndexChanged.connect(self.on_value_changed)

        self._set_combo_for_accent(self.selected_accent)
        self._update_preview()

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
        self.themeChanged.emit(self.selected_mode, self.selected_accent)

    def pick_custom_color(self):
        color = QColorDialog.getColor(QColor(self.selected_accent), self, "Akzentfarbe wählen")
        if color.isValid():
            self.selected_accent = color.name()
            self.accent_combo.setCurrentIndex(self.accent_combo.count() - 1)
            self._update_preview()
            self.themeChanged.emit(self.selected_mode, self.selected_accent)
