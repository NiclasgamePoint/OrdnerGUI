"""Client-only settings dialog composed from focused Qt pages."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.presentation.settings import (
    ClientSettingsPresenter,
    ClientSettingsViewModel,
    SourceMappingViewModel,
)


class ConnectionSettingsPage(QWidget):
    def __init__(self, model: ClientSettingsViewModel):
        super().__init__()
        layout = QFormLayout(self)
        self.server_url = QLineEdit(model.server_url)
        self.server_url.setPlaceholderText("https://papagui-server.example")
        self.api_token = QLineEdit(model.api_token)
        self.api_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_token.setPlaceholderText("Client-Token")
        layout.addRow("Server-URL", self.server_url)
        layout.addRow("API-Token", self.api_token)
        self._mark_override(self.server_url, "server_url", model)
        self._mark_override(self.api_token, "api_token", model)

    @staticmethod
    def _mark_override(widget: QWidget, field: str, model: ClientSettingsViewModel) -> None:
        if field in model.environment_overrides:
            widget.setEnabled(False)
            widget.setToolTip("Dieser Wert wird durch eine Umgebungsvariable überschrieben.")


class SourceMappingsSettingsPage(QWidget):
    HEADERS = ("source_id", "Windows", "macOS", "Linux")

    def __init__(self, model: ClientSettingsViewModel):
        super().__init__()
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Ordne jede serverseitige source_id einem lokalen NAS-/SMB-Pfad zu. "
            "Nicht benötigte Plattformfelder dürfen leer bleiben."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        layout.addWidget(self.table)
        for mapping in model.mappings:
            self.add_mapping(mapping)
        buttons = QHBoxLayout()
        add = QPushButton("Zuordnung hinzufügen")
        remove = QPushButton("Ausgewählte entfernen")
        add.clicked.connect(lambda: self.add_mapping())
        remove.clicked.connect(self.remove_selected)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout.addLayout(buttons)
        if "source_mappings" in model.environment_overrides:
            self.table.setEnabled(False)
            add.setEnabled(False)
            remove.setEnabled(False)
            self.table.setToolTip(
                "Pfadzuordnungen werden durch PAPAGUI_SOURCE_MAPPINGS überschrieben."
            )

    def add_mapping(self, mapping: SourceMappingViewModel | None = None) -> None:
        value = mapping or SourceMappingViewModel("")
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, text in enumerate(
            (value.source_id, value.windows, value.macos, value.linux)
        ):
            self.table.setItem(row, column, QTableWidgetItem(text))

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def mappings(self) -> tuple[SourceMappingViewModel, ...]:
        def text(row: int, column: int) -> str:
            item = self.table.item(row, column)
            return item.text() if item is not None else ""

        return tuple(
            SourceMappingViewModel(*(text(row, column) for column in range(4)))
            for row in range(self.table.rowCount())
        )


class SyncAppearanceSettingsPage(QWidget):
    def __init__(self, model: ClientSettingsViewModel):
        super().__init__()
        layout = QFormLayout(self)
        interval = QHBoxLayout()
        self.interval_value = QSpinBox()
        self.interval_unit = QComboBox()
        self.interval_unit.addItems(["Minuten", "Stunden"])
        self.interval_unit.currentTextChanged.connect(self._update_range)
        interval.addWidget(self.interval_value)
        interval.addWidget(self.interval_unit)
        layout.addRow("Lokaler Datenabgleich", interval)
        self.theme = QComboBox()
        self.theme.addItem("System", ClientTheme.SYSTEM.value)
        self.theme.addItem("Hell", ClientTheme.LIGHT.value)
        self.theme.addItem("Dunkel", ClientTheme.DARK.value)
        layout.addRow("Darstellung", self.theme)
        self.interval_unit.setCurrentText(model.interval_unit)
        self._update_range(model.interval_unit)
        self.interval_value.setValue(model.interval_value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(model.theme)))
        if "sync_interval_seconds" in model.environment_overrides:
            self.interval_value.setEnabled(False)
            self.interval_unit.setEnabled(False)
        if "theme" in model.environment_overrides:
            self.theme.setEnabled(False)

    def _update_range(self, unit: str) -> None:
        if unit == "Stunden":
            self.interval_value.setRange(1, 48)
        else:
            self.interval_value.setRange(15, 2880)


class ClientSettingsDialog(QDialog):
    def __init__(
        self,
        current: ClientSettings,
        *,
        sources=None,
        presenter: ClientSettingsPresenter | None = None,
        onboarding: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._current = current
        self._presenter = presenter or ClientSettingsPresenter()
        model = self._presenter.present(current, sources)
        self.setWindowTitle(
            "PapaGUI einrichten" if onboarding or model.onboarding_required else "Client-Einstellungen"
        )
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        if onboarding or model.onboarding_required:
            notice = QLabel(
                "Für Dateivorschauen fehlt noch mindestens eine source_id-Pfadzuordnung."
            )
            notice.setWordWrap(True)
            notice.setObjectName("OnboardingNotice")
            layout.addWidget(notice)
        tabs = QTabWidget()
        self.connection_page = ConnectionSettingsPage(model)
        self.mapping_page = SourceMappingsSettingsPage(model)
        self.sync_page = SyncAppearanceSettingsPage(model)
        tabs.addTab(self.connection_page, "Verbindung")
        tabs.addTab(self.mapping_page, "Pfadzuordnungen")
        tabs.addTab(self.sync_page, "Synchronisation & Design")
        layout.addWidget(tabs)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def settings(self) -> ClientSettings:
        return self._presenter.build(
            current=self._current,
            server_url=self.connection_page.server_url.text(),
            api_token=self.connection_page.api_token.text(),
            mappings=self.mapping_page.mappings(),
            interval_value=self.sync_page.interval_value.value(),
            interval_unit=self.sync_page.interval_unit.currentText(),
            theme=str(self.sync_page.theme.currentData()),
        )

    def _validate_and_accept(self) -> None:
        try:
            self.settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Einstellungen prüfen", str(exc))
            return
        self.accept()


DARK_STYLE = """
QWidget { background: #17242d; color: #dceaf0; }
QLineEdit, QComboBox, QSpinBox, QTableWidget { background: #223540; color: #f1f7f9; border: 1px solid #486575; }
QTabWidget::pane { border-color: #486575; background: #17242d; }
QTabBar::tab { background: #29404c; color: #dceaf0; }
QTabBar::tab:selected { background: #17242d; color: #4ed8c4; }
"""


def theme_stylesheet(theme: ClientTheme | str, base_style: str) -> str:
    selected = ClientTheme(theme)
    return base_style + DARK_STYLE if selected is ClientTheme.DARK else base_style
