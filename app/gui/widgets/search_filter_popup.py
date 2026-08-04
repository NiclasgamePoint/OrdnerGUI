from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from app.gui.widgets.buttons import AppButton
from app.core.search_models import SearchSort


class SearchFilterPopup(QFrame):
    """Compact popup containing all search filters."""

    filtersChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint,
        )
        self.setObjectName("SearchFilterPopup")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(340)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("SearchFilterBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("Suchfilter")
        title.setObjectName("PopupSectionTitle")
        layout.addWidget(title)

        self.domain_combo = self._add_filter(layout, "Fachthema")
        self.year_combo = self._add_filter(layout, "Jahre")
        self.file_type_combo = self._add_filter(layout, "Dateityp")
        self.sort_combo = self._add_filter(layout, "Sortierung")
        self.sort_combo.addItem("Relevanz", SearchSort.RELEVANCE)
        self.sort_combo.addItem("Datum", SearchSort.DATE)
        self.sort_combo.addItem("Alphabet", SearchSort.ALPHABETICAL)
        self.sort_combo.setAccessibleName("Sortierung der Suchergebnisse")
        self.sort_combo.setAccessibleDescription(
            "Sortiert alle Ergebnisbereiche nach Relevanz, Datum oder Alphabet."
        )
        self.include_subfolders_checkbox = QCheckBox(
            "Unterordner in der Ordnersuche einbeziehen"
        )
        self.include_subfolders_checkbox.setAccessibleName(
            "Unterordner einbeziehen"
        )
        self.include_subfolders_checkbox.setAccessibleDescription(
            "Berücksichtigt Namen strukturierter Projektunterordner bei der Suche."
        )
        layout.addWidget(self.include_subfolders_checkbox)

        actions = QHBoxLayout()
        self.clear_button = AppButton("Zurücksetzen", AppButton.SECONDARY)
        apply_button = AppButton("Übernehmen")
        self.clear_button.clicked.connect(self.clear_filters)
        apply_button.clicked.connect(self.close)
        actions.addWidget(self.clear_button)
        actions.addStretch()
        actions.addWidget(apply_button)
        layout.addLayout(actions)
        outer_layout.addWidget(body)

        for combo in self.combos:
            combo.currentIndexChanged.connect(self.filtersChanged.emit)
        self.sort_combo.currentIndexChanged.connect(self.filtersChanged.emit)
        self.include_subfolders_checkbox.toggled.connect(self.filtersChanged.emit)

    @property
    def combos(self) -> tuple[QComboBox, QComboBox, QComboBox]:
        return self.domain_combo, self.year_combo, self.file_type_combo

    def _add_filter(self, layout: QVBoxLayout, label_text: str) -> QComboBox:
        label = QLabel(label_text)
        label.setObjectName("PopupCaption")
        layout.addWidget(label)
        combo = QComboBox()
        combo.setAccessibleName(label_text)
        combo.setAccessibleDescription(f"Suchergebnisse nach {label_text} filtern.")
        combo.setMinimumHeight(34)
        layout.addWidget(combo)
        return combo

    def clear_filters(self):
        changed = False
        for combo in self.combos:
            if combo.currentIndex() > 0:
                changed = True
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        if self.sort_combo.currentData() != SearchSort.RELEVANCE:
            changed = True
        self.sort_combo.blockSignals(True)
        self.sort_combo.setCurrentIndex(
            self.sort_combo.findData(SearchSort.RELEVANCE)
        )
        self.sort_combo.blockSignals(False)
        if self.include_subfolders_checkbox.isChecked():
            changed = True
        self.include_subfolders_checkbox.blockSignals(True)
        self.include_subfolders_checkbox.setChecked(False)
        self.include_subfolders_checkbox.blockSignals(False)
        if changed:
            self.filtersChanged.emit()

    def active_filter_count(self) -> int:
        return (
            sum(bool(combo.currentData()) for combo in self.combos)
            + int(self.sort_combo.currentData() != SearchSort.RELEVANCE)
            + int(self.include_subfolders_checkbox.isChecked())
        )
