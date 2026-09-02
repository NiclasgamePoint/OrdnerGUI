"""Bounded, searchable spreadsheet preview widget."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from papagui_client.viewers.spreadsheets import SpreadsheetPreviewReader

from .buttons import viewer_button


def _column_label(index: int) -> str:
    """Return Excel-style labels without importing the workbook backend in Qt."""
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


class SpreadsheetViewerWidget(QWidget):
    def __init__(
        self,
        reader: SpreadsheetPreviewReader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerContent")
        self._reader = reader or SpreadsheetPreviewReader()
        self.path: Path | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Tabellenblatt:"))
        self.sheet_combo = QComboBox()
        self.sheet_combo.currentIndexChanged.connect(self._load_current_sheet)
        toolbar.addWidget(self.sheet_combo)
        toolbar.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("In Tabelle suchen …")
        self.search_input.setMaximumWidth(210)
        self.search_button = viewer_button("Weiter")
        self.search_input.returnPressed.connect(self.find_next)
        self.search_button.clicked.connect(self.find_next)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.search_button)
        layout.addLayout(toolbar)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.info_label = QLabel()
        self.info_label.setObjectName("ViewerMeta")
        layout.addWidget(self.info_label)

    def load(self, path: Path) -> None:
        names = self._reader.sheet_names(path)
        if not names:
            raise ValueError("Die Arbeitsmappe enthält keine Tabellenblätter")
        self.path = path
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.addItems(names)
        self.sheet_combo.blockSignals(False)
        self._load_current_sheet()

    def _load_current_sheet(self) -> None:
        if self.path is None or self.sheet_combo.currentIndex() < 0:
            return
        preview = self._reader.read_sheet(self.path, self.sheet_combo.currentText())
        rows = preview.displayed_rows
        columns = preview.displayed_columns
        self.table.setRowCount(rows)
        self.table.setColumnCount(columns)
        self.table.setHorizontalHeaderLabels(
            [_column_label(index) for index in range(1, columns + 1)]
        )
        for row_index, row in enumerate(preview.values):
            for column_index, value in enumerate(row):
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                text = "" if value is None else str(value)
                self.table.setItem(row_index, column_index, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        message = (
            f"{preview.total_rows} Zeilen · {preview.total_columns} Spalten · "
            f"angezeigt bis {rows} × {columns}"
        )
        if preview.truncated:
            message += (
                f" · Hinweis: Anzeige ist auf {preview.maximum_rows} Zeilen und "
                f"{preview.maximum_columns} Spalten begrenzt."
            )
        self.info_label.setText(message)

    def find_next(self) -> None:
        query = self.search_input.text().strip().casefold()
        columns = self.table.columnCount()
        cells = self.table.rowCount() * columns
        if not query or not cells or not columns:
            return
        current = self.table.currentRow() * columns + self.table.currentColumn() + 1
        for step in range(cells):
            row, column = divmod((current + step) % cells, columns)
            item = self.table.item(row, column)
            if item is not None and query in item.text().casefold():
                self.table.setCurrentItem(item)
                self.table.scrollToItem(item)
                return
