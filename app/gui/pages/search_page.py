from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.customer_models import Customer
from app.gui.widgets.result_row import ResultRow


class _ResultSection(QWidget):
    activated = Signal(object)
    openPathRequested = Signal(str)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.heading = QLabel(title)
        self.heading.setObjectName("SearchSectionTitle")
        self._layout.addWidget(self.heading)
        self._message = QLabel("")
        self._message.setObjectName("SearchSectionMessage")
        self._message.setWordWrap(True)
        self._layout.addWidget(self._message)
        self._rows: list[ResultRow] = []

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def set_message(self, message: str):
        self._clear_rows()
        self.heading.setText(self._title)
        self._message.setText(message)
        self._message.setVisible(bool(message))

    def set_rows(self, rows: list[ResultRow], total: int):
        self._clear_rows()
        self.heading.setText(f"{self._title} ({total})")
        self._message.setVisible(not rows)
        self._message.setText("Keine Treffer gefunden" if not rows else "")
        self._rows = rows
        for row in rows:
            row.activated.connect(self.activated.emit)
            row.openPathRequested.connect(self.openPathRequested.emit)
            self._layout.addWidget(row)

    def _clear_rows(self):
        for row in self._rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()


class SearchPage(QWidget):
    """Scrollable overview containing only customer and folder results."""

    customerActivated = Signal(int)
    folderActivated = Signal(str)
    openPathRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SearchPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        card = QWidget()
        card.setObjectName("PageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(12)

        title = QLabel("Suchergebnisse")
        title.setObjectName("PageTitle")
        card_layout.addWidget(title)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("SearchResultsScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("SearchResultsContent")
        self.results_layout = QVBoxLayout(content)
        self.results_layout.setContentsMargins(2, 2, 6, 2)
        self.results_layout.setSpacing(16)

        self.customer_section = _ResultSection("Kunden")
        self.folder_section = _ResultSection("Ordner")
        self.customer_section.activated.connect(
            lambda value: self.customerActivated.emit(int(value))
        )
        self.folder_section.activated.connect(
            lambda value: self.folderActivated.emit(str(value))
        )
        self.customer_section.openPathRequested.connect(self.openPathRequested.emit)
        self.folder_section.openPathRequested.connect(self.openPathRequested.emit)
        self.results_layout.addWidget(self.customer_section)
        self.results_layout.addWidget(self.folder_section)
        self.results_layout.addStretch(1)

        self.scroll_area.setWidget(content)
        card_layout.addWidget(self.scroll_area, 1)
        layout.addWidget(card, 1)

        self.reset()

    def reset(self, customers: list[Customer] | None = None):
        if customers is None:
            self.customer_section.set_message("Suchbegriff eingeben")
        else:
            self.set_customers(customers, len(customers))
        self.folder_section.set_message("Suchbegriff eingeben")

    def prepare_search(self):
        self.customer_section.set_message("Kundensuche läuft …")
        self.folder_section.set_message("Ordnersuche läuft …")

    def show_short_query_hint(self):
        self.customer_section.set_message("Mindestens zwei Zeichen eingeben")
        self.folder_section.set_message("Mindestens zwei Zeichen eingeben")

    def set_customer_error(self, error: str):
        self.customer_section.set_message(f"Kundensuche fehlgeschlagen: {error}")

    def set_folder_error(self, error: str):
        self.folder_section.set_message(f"Ordnersuche fehlgeschlagen: {error}")

    def set_customers(self, customers: list[Customer], total: int):
        rows = []
        for customer in customers:
            folders = customer.folder_paths or (
                [customer.folder_path] if customer.folder_path else []
            )
            details = [customer.entity_type or "Kunde"]
            if customer.service_types:
                details.append(", ".join(customer.service_types))
            details.append(f"{len(folders)} Ordner")
            rows.append(
                ResultRow(
                    customer.display_name,
                    " · ".join(details),
                    customer.id,
                    folders[0] if folders else "",
                )
            )
        self.customer_section.set_rows(rows, total)

    def set_folders(self, folders: list[dict], total: int):
        rows = []
        for folder in folders:
            relative_path = str(folder.get("relative_path") or "")
            file_count = int(folder.get("file_count") or 0)
            subtitle = f"{file_count} Dateien"
            if relative_path:
                subtitle += f" · {relative_path}"
            path = str(folder.get("folder_path") or "")
            rows.append(
                ResultRow(
                    str(folder.get("folder_name") or "Unbekannter Ordner"),
                    subtitle,
                    path,
                    path,
                )
            )
        self.folder_section.set_rows(rows, total)
