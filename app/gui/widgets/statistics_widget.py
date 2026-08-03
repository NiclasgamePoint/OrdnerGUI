from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from app.core.statistics import ApplicationStatistics


class StatisticsWidget(QFrame):
    """Reusable presentation for home and settings statistics."""

    def __init__(self, compact: bool = False, parent=None):
        super().__init__(parent)
        self.compact = compact
        self.setObjectName("StatisticsWidget")
        self.setAccessibleName(
            "Startseitenstatistik" if compact else "Anwendungsstatistik"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.status_label = QLabel("Statistik wird geladen …")
        self.status_label.setObjectName("PopupCaption")
        layout.addWidget(self.status_label)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(20)
        self.grid.setVerticalSpacing(7)
        layout.addLayout(self.grid)
        self._value_labels: dict[str, QLabel] = {}

    def set_loading(self):
        self._clear()
        self.status_label.setText("Statistik wird geladen …")
        self.status_label.setVisible(True)

    def set_error(self, error: str):
        self._clear()
        self.status_label.setText(f"Statistik konnte nicht geladen werden: {error}")
        self.status_label.setVisible(True)

    def set_statistics(self, statistics: ApplicationStatistics):
        self._clear()
        self.status_label.clear()
        self.status_label.setVisible(False)
        fields = [
            ("customer_count", "Kunden", statistics.customer_count),
            ("project_count", "Projektordner", statistics.project_count),
        ]
        if not self.compact:
            fields.extend([
                ("contact_count", "Kontakte", statistics.contact_count),
                ("service_count", "Dienstleistungen", statistics.service_count),
                ("file_count", "Indexierte Dateien", statistics.file_count),
                (
                    "total_file_size",
                    "Datenmenge",
                    self._format_size(statistics.total_file_size),
                ),
                (
                    "content_count",
                    "Volltextdokumente",
                    statistics.content_count,
                ),
                (
                    "pending_recognition_count",
                    "Offene Prüffälle",
                    statistics.pending_recognition_count,
                ),
                (
                    "last_indexed_at",
                    "Letzter Indexlauf",
                    self._format_timestamp(statistics.last_indexed_at),
                ),
                (
                    "last_index_duration_seconds",
                    "Dauer des Indexlaufs",
                    f"{statistics.last_index_duration_seconds:.2f} Sekunden",
                ),
            ])
        columns = 2 if self.compact else 1
        for position, (key, label_text, value) in enumerate(fields):
            row = position // columns
            column = (position % columns) * 2
            label = QLabel(label_text)
            label.setObjectName("StatCaption")
            value_label = QLabel(str(value))
            value_label.setObjectName("StatisticValue")
            self.grid.addWidget(label, row, column)
            self.grid.addWidget(value_label, row, column + 1)
            self._value_labels[key] = value_label
        description = ", ".join(
            f"{label}: {value}" for _, label, value in fields
        )
        self.setAccessibleDescription(description)

    def _clear(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._value_labels.clear()

    @staticmethod
    def _format_size(size: int) -> str:
        if size >= 1024 ** 3:
            return f"{size / 1024 ** 3:.2f} GB"
        if size >= 1024 ** 2:
            return f"{size / 1024 ** 2:.2f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    @staticmethod
    def _format_timestamp(value: str) -> str:
        if not value:
            return "Noch nicht ausgeführt"
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return value
