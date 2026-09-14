"""Client settings in the original PapaGUI card-and-navigation design.

The window deliberately contains client concerns only. Index creation,
recognition configuration and maintenance remain server responsibilities and
are merely explained here so the old information architecture does not blur
the client/server boundary again.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QColorDialog,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.gui.settings_help import SettingsHelpController
from papagui_client.gui.theme import AppearanceSettings, ThemeManager, build_stylesheet
from papagui_client.gui.legacy_models import ApplicationStatistics
from papagui_client.gui.widgets import AppButton, StatisticsWidget
from papagui_client.gui.widgets.click_activated_inputs import (
    ClickActivatedComboBox,
    ClickActivatedSlider,
    ClickActivatedSpinBox,
)
from papagui_client.presentation.settings import (
    ClientSettingsPresenter,
    ClientSettingsViewModel,
    SourceMappingViewModel,
)


SETTINGS_HELP_TEXTS = {
    "server_url": (
        "Adresse des zentralen PapaGUI-Servers. Der Client bezieht von dort "
        "Generationen sowie Kunden- und Journaländerungen."
    ),
    "api_token": (
        "Client-Token für die Server-API. Es wird vom lokalen Komfortstart "
        "automatisch erzeugt und für Client und Server gemeinsam verwendet."
    ),
    "sync_interval": (
        "Zeitabstand, in dem der Client eine neue, vollständig geprüfte "
        "Servergeneration abruft. Zulässig sind 15 Minuten bis 48 Stunden."
    ),
    "source_mappings": (
        "Ordnet serverseitige source_id-Werte den lokalen NAS-/SMB-Pfaden für "
        "Windows, macOS und Linux zu."
    ),
    "appearance_mode": "Wechselt zwischen System-, heller und dunkler Oberfläche.",
    "appearance_accent": "Wählt die Akzentfarbe für Hervorhebungen und Aktionen.",
    "appearance_contrast": "Passt den Kontrast der Oberflächenfarben an.",
    "appearance_font_size": (
        "Ändert Schriftgröße und die mitwachsenden Bedienelemente der Anwendung."
    ),
}


def _page_layout(page: QWidget, *, spacing: int = 12) -> QVBoxLayout:
    page.setObjectName("SettingsPage")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setSpacing(spacing)
    return layout


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PopupSectionTitle")
    return label


def _caption(text: str, *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PopupCaption")
    label.setWordWrap(wrap)
    return label


class ConnectionSettingsPage(QWidget):
    """General client/server connection settings."""

    def __init__(
        self,
        model: ClientSettingsViewModel,
        current: ClientSettings,
        help_controller: SettingsHelpController | None = None,
        *,
        onboarding: bool = False,
    ) -> None:
        super().__init__()
        layout = _page_layout(self)
        layout.addWidget(_heading("Allgemein"))

        if onboarding or model.onboarding_required:
            notice = QLabel(
                "Für Dateivorschauen fehlt noch mindestens eine "
                "source_id-Pfadzuordnung. Bitte Server und Pfade vollständig einrichten."
            )
            notice.setObjectName("OnboardingNotice")
            notice.setWordWrap(True)
            layout.addWidget(notice)

        layout.addWidget(_caption("Serververbindung", wrap=False))
        layout.addWidget(
            _caption(
                "Der Desktop-Client liest Index und Kundendaten ausschließlich über den "
                "zentralen Server. Der letzte gültige lokale Stand bleibt offline nutzbar."
            )
        )

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        self.server_url = QLineEdit(model.server_url)
        self.server_url.setPlaceholderText("https://papagui-server.example")
        self.server_url.setAccessibleName("Server-URL")
        server_label = _caption("Server-URL", wrap=False)
        form.addRow(server_label, self.server_url)

        self.api_token = QLineEdit(model.api_token)
        self.api_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_token.setPlaceholderText("Client-Token")
        self.api_token.setAccessibleName("API-Token")
        token_label = _caption("API-Token", wrap=False)
        form.addRow(token_label, self.api_token)
        layout.addLayout(form)

        if help_controller is not None:
            help_controller.register(
                "server_url", SETTINGS_HELP_TEXTS["server_url"], server_label, self.server_url
            )
            help_controller.register(
                "api_token", SETTINGS_HELP_TEXTS["api_token"], token_label, self.api_token
            )

        self._mark_override(self.server_url, "server_url", model)
        self._mark_override(self.api_token, "api_token", model)

        layout.addWidget(_caption("Lokaler Client-Speicher", wrap=False))
        self.data_root = QLineEdit(str(current.data_root))
        self.data_root.setReadOnly(True)
        self.data_root.setAccessibleName("Lokaler Client-Speicher")
        self.data_root.setToolTip(
            "Enthält lokale Generationen, den atomaren Aktivzeiger und die Offline-Outbox."
        )
        layout.addWidget(self.data_root)
        layout.addWidget(
            _caption(
                "Der Speicherort wird beim Programmstart festgelegt. Ein Wechsel erfordert "
                "einen Neustart; der Client erzeugt dort niemals selbst einen Index."
            )
        )
        layout.addStretch()

    @staticmethod
    def _mark_override(widget: QWidget, field: str, model: ClientSettingsViewModel) -> None:
        if field in model.environment_overrides:
            widget.setEnabled(False)
            widget.setToolTip("Dieser Wert wird durch eine Umgebungsvariable überschrieben.")


class SourceMappingsSettingsPage(QWidget):
    """Cross-platform source mappings presented on the historic Search page."""

    HEADERS = ("source_id", "Windows", "macOS", "Linux")

    def __init__(
        self,
        model: ClientSettingsViewModel,
        help_controller: SettingsHelpController | None = None,
    ) -> None:
        super().__init__()
        layout = _page_layout(self)
        layout.addWidget(_heading("Suche"))
        layout.addWidget(
            _caption(
                "Die Suchergebnisse verwenden serverseitige relative Pfade. Ordne jede "
                "source_id dem lokalen NAS-/SMB-Pfad zu, damit Ordner, Vorschau und "
                "Dokumente auf diesem Rechner geöffnet werden können."
            )
        )

        mappings_label = _caption("Lokale Pfadzuordnungen", wrap=False)
        layout.addWidget(mappings_label)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setObjectName("SourceMappingsTable")
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._columns_sized = False
        self.table.itemChanged.connect(self._mapping_item_changed)
        layout.addWidget(self.table, 1)
        for mapping in model.mappings:
            self.add_mapping(mapping)

        buttons = QHBoxLayout()
        self.add_button = AppButton("Zuordnung hinzufügen", AppButton.SECONDARY)
        self.remove_button = AppButton("Ausgewählte entfernen", AppButton.DANGER)
        self.add_button.clicked.connect(lambda: self.add_mapping())
        self.remove_button.clicked.connect(self.remove_selected)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        if help_controller is not None:
            help_controller.register(
                "source_mappings",
                SETTINGS_HELP_TEXTS["source_mappings"],
                mappings_label,
                self.table,
            )

        if "source_mappings" in model.environment_overrides:
            self.table.setEnabled(False)
            self.add_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.table.setToolTip(
                "Pfadzuordnungen werden durch PAPAGUI_SOURCE_MAPPINGS überschrieben."
            )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._columns_sized:
            # Size after the settings theme is inherited. Empty platform columns
            # need only their headers; populated paths keep their natural width.
            self.table.resizeColumnsToContents()
            self._columns_sized = True

    def _mapping_item_changed(self, item: QTableWidgetItem) -> None:
        if item.toolTip() == item.text():
            return
        item.setToolTip(item.text())
        self.table.resizeColumnToContents(item.column())

    def add_mapping(self, mapping: SourceMappingViewModel | None = None) -> None:
        value = mapping or SourceMappingViewModel("")
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, text in enumerate(
            (value.source_id, value.windows, value.macos, value.linux)
        ):
            self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.setCurrentCell(row, 0)

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
    """Local generation synchronization in the old Indexierung layout."""

    indexServerRequested = Signal()

    def __init__(
        self,
        model: ClientSettingsViewModel,
        current: ClientSettings,
        help_controller: SettingsHelpController | None = None,
    ) -> None:
        super().__init__()
        layout = _page_layout(self)
        layout.addWidget(_heading("Lokale Suche und Synchronisation"))
        layout.addWidget(
            _caption(
                "Indexaufbau, OCR, Kundenerkennung und Wartung werden im separaten "
                "Indexserver-Fenster über das Systemtray verwaltet. Dieser Client lädt "
                "ausschließlich fertige und geprüfte Generationen herunter."
            )
        )

        server_row = QHBoxLayout()
        self.open_index_server_button = AppButton(
            "Indexserver öffnen",
            AppButton.SECONDARY,
        )
        self.open_index_server_button.setAccessibleName("Indexserver öffnen")
        self.open_index_server_button.setToolTip(
            "Öffnet den unabhängigen Indexserver-Tray; der Client führt selbst "
            "keinen Indexlauf aus."
        )
        self.open_index_server_button.clicked.connect(self.indexServerRequested.emit)
        server_row.addWidget(self.open_index_server_button)
        server_row.addStretch()
        layout.addLayout(server_row)

        layout.addWidget(_caption("Automatischer Datenabgleich", wrap=False))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        interval_widget = QWidget()
        interval_layout = QHBoxLayout(interval_widget)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        interval_layout.setSpacing(8)
        self.interval_value = ClickActivatedSpinBox()
        self.interval_value.setAccessibleName("Dauer zwischen Synchronisierungen")
        self.interval_unit = ClickActivatedComboBox()
        self.interval_unit.setAccessibleName("Einheit des Synchronisationsintervalls")
        self.interval_unit.addItems(["Minuten", "Stunden"])
        self.interval_unit.currentTextChanged.connect(self._update_range)
        interval_layout.addWidget(self.interval_value, 1)
        interval_layout.addWidget(self.interval_unit)
        interval_label = _caption("Alle", wrap=False)
        form.addRow(interval_label, interval_widget)
        layout.addLayout(form)

        self.interval_unit.setCurrentText(model.interval_unit)
        self._update_range(model.interval_unit)
        self.interval_value.setValue(model.interval_value)
        if help_controller is not None:
            help_controller.register(
                "sync_interval",
                SETTINGS_HELP_TEXTS["sync_interval"],
                interval_label,
                self.interval_value,
                self.interval_unit,
            )
        if "sync_interval_seconds" in model.environment_overrides:
            self.interval_value.setEnabled(False)
            self.interval_unit.setEnabled(False)
            interval_widget.setToolTip(
                "Das Intervall wird durch PAPAGUI_SYNC_INTERVAL_SECONDS überschrieben."
            )

        retention = QFrame()
        retention.setObjectName("IndexControlCard")
        retention_layout = QVBoxLayout(retention)
        retention_title = QLabel("Lokale Generationen")
        retention_title.setObjectName("PopupSectionTitle")
        retention_layout.addWidget(retention_title)
        retention_layout.addWidget(
            _caption(
                "Aktiver Stand plus drei vorherige gültige Generationen. Beschädigte "
                "Downloads werden verworfen und verändern weder Aktivstand noch Backups."
            )
        )
        cache = QLineEdit(str(current.cache_root))
        cache.setReadOnly(True)
        cache.setAccessibleName("Lokaler Generationsspeicher")
        retention_layout.addWidget(cache)
        layout.addWidget(retention)
        layout.addStretch()

        # The selector is attached by ClientSettingsDialog's Aussehen page.
        # Keeping it here preserves the 0.4.2 public API (sync_page.theme).
        self.theme: ClickActivatedComboBox

    def _update_range(self, unit: str) -> None:
        if unit == "Stunden":
            self.interval_value.setRange(1, 48)
        else:
            self.interval_value.setRange(15, 2880)


class _ServerOwnedRecognitionPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = _page_layout(self)
        layout.addWidget(_heading("Automatische Kundenerkennung"))
        layout.addWidget(
            _caption(
                "Die Kundenerkennung läuft zusammen mit dem Indexjob auf dem Server. "
                "Manuell gepflegte Kundendaten werden dabei nicht still überschrieben."
            )
        )
        card = QFrame()
        card.setObjectName("IndexControlCard")
        card_layout = QFormLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.addRow(_caption("Ausführung", wrap=False), QLabel("Serverseitig"))
        card_layout.addRow(_caption("Prüffälle", wrap=False), QLabel("Über die Kundenansicht"))
        card_layout.addRow(
            _caption("Konfiguration", wrap=False), QLabel("Indexserver-Fenster im Systemtray")
        )
        layout.addWidget(card)
        layout.addWidget(
            _caption(
                "Erkennungsregeln, Dokumentmuster und Blacklists sind serverweite Werte. "
                "Sie werden deshalb nicht als lokale Clienteinstellung gespeichert."
            )
        )
        layout.addStretch()


class _ClientStatisticsPage(QWidget):
    def __init__(
        self,
        model: ClientSettingsViewModel,
        current: ClientSettings,
        statistics: ApplicationStatistics | None,
    ) -> None:
        super().__init__()
        layout = _page_layout(self)
        layout.addWidget(_heading("Statistik"))
        layout.addWidget(
            _caption(
                "Kernkennzahlen aus der lokal verfügbaren Kundenkopie und dem aktiven "
                "Suchindex. Server- und Jobstatus stehen im Indexserver-Fenster."
            )
        )

        self.statistics_widget = StatisticsWidget()
        layout.addWidget(self.statistics_widget)
        self.set_statistics(statistics)

        config_heading = _caption("Lokale Clientkonfiguration", wrap=False)
        layout.addWidget(config_heading)
        card = QFrame()
        card.setObjectName("IndexControlCard")
        form = QFormLayout(card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setHorizontalSpacing(18)
        form.addRow(_caption("Server", wrap=False), QLabel(model.server_url or "Nicht eingerichtet"))
        form.addRow(_caption("Pfadzuordnungen", wrap=False), QLabel(str(len(model.mappings))))
        form.addRow(
            _caption("Synchronisation", wrap=False),
            QLabel(f"Alle {model.interval_value} {model.interval_unit}"),
        )
        local_root = QLabel(str(current.data_root))
        local_root.setWordWrap(True)
        local_root.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(_caption("Lokaler Speicher", wrap=False), local_root)
        layout.addWidget(card)
        layout.addStretch()

    def set_statistics(
        self,
        statistics: ApplicationStatistics | None,
        error: str = "",
    ) -> None:
        if error:
            self.statistics_widget.set_error(error)
        elif statistics is None:
            self.statistics_widget.set_loading()
        else:
            self.statistics_widget.set_statistics(statistics)


class ClientSettingsDialog(QDialog):
    """Modal client settings window with the complete v0.4.1 visual shell."""

    indexServerRequested = Signal()

    NAVIGATION = (
        "Allgemein",
        "Indexierung",
        "Suche",
        "Kundenerkennung",
        "Statistik",
        "Aussehen",
    )

    def __init__(
        self,
        current: ClientSettings,
        *,
        sources=None,
        presenter: ClientSettingsPresenter | None = None,
        onboarding: bool = False,
        statistics: ApplicationStatistics | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self._current = current
        self._presenter = presenter or ClientSettingsPresenter()
        self._model = self._presenter.present(current, sources)
        self._color_dialog_active = False
        self._theme_manager = ThemeManager()
        self._theme_manager.set_mode(self._effective_mode(current.theme))

        self.setObjectName("ClientSettingsDialog")
        self.setWindowTitle(
            "PapaGUI einrichten"
            if onboarding or self._model.onboarding_required
            else "Client-Einstellungen"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(640, 480)
        self.resize(self.size_for_parent())
        self.setStyleSheet(
            build_stylesheet(
                self._theme_manager.mode,
                self._theme_manager.accent,
                self._theme_manager.contrast,
                self._theme_manager.font_size,
            )
        )
        self.help_controller = SettingsHelpController(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        popup = QFrame()
        popup.setObjectName("SettingsPopup")
        popup_layout = QVBoxLayout(popup)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        body = QFrame()
        body.setObjectName("SettingsBody")
        content_layout = QVBoxLayout(body)
        content_layout.setContentsMargins(18, 16, 18, 14)
        content_layout.setSpacing(14)
        is_onboarding = onboarding or self._model.onboarding_required
        title = QLabel("Willkommen bei PapaGUI" if is_onboarding else "Einstellungen")
        title.setObjectName("PopupTitle")
        content_layout.addWidget(title)
        if is_onboarding:
            content_layout.addWidget(
                _caption(
                    "Verbinde diesen Client mit dem zentralen Indexserver und ordne "
                    "die dort veröffentlichten Datenquellen deinen lokalen NAS-/SMB-Pfaden zu."
                )
            )

        split = QHBoxLayout()
        split.setSpacing(16)
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("SettingsNav")
        font_size = self._theme_manager.font_size
        self.nav_list.setMinimumWidth(max(184, font_size * 14))
        self.nav_list.setSpacing(2)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setAccessibleName("Einstellungsbereiche")
        item_size = QSize(max(164, font_size * 12), max(42, font_size * 3))
        for text in self.NAVIGATION:
            item = QListWidgetItem(text)
            item.setSizeHint(item_size)
            self.nav_list.addItem(item)
        split.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        self.stack.setObjectName("SettingsStack")
        self.stack.setAccessibleName("Einstellungsinhalt")
        self.connection_page = ConnectionSettingsPage(
            self._model, current, self.help_controller, onboarding=onboarding
        )
        self.sync_page = SyncAppearanceSettingsPage(
            self._model, current, self.help_controller
        )
        self.sync_page.indexServerRequested.connect(self.indexServerRequested.emit)
        self.mapping_page = SourceMappingsSettingsPage(
            self._model, self.help_controller
        )
        self.recognition_page = _ServerOwnedRecognitionPage()
        self.statistics_page = _ClientStatisticsPage(
            self._model,
            current,
            statistics,
        )
        self.appearance_page = self._build_appearance_page(self._model)
        for page in (
            self.connection_page,
            self.sync_page,
            self.mapping_page,
            self.recognition_page,
            self.statistics_page,
            self.appearance_page,
        ):
            self.stack.addWidget(page)
        split.addWidget(self.stack, 1)
        content_layout.addLayout(split, 1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.cancel_button = AppButton("Abbrechen", AppButton.SECONDARY, minimum_width=96)
        self.save_button = AppButton("Speichern", minimum_width=96)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._validate_and_accept)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)
        content_layout.addLayout(button_row)
        popup_layout.addWidget(body)
        outer.addWidget(popup)
        self.nav_list.currentRowChanged.connect(self._show_page)
        self.nav_list.setCurrentRow(0)

        # Compatibility handle without reintroducing the newer tab/button-box look.
        self.buttons = _DialogButtonsFacade(self.save_button, self.cancel_button)

    def set_statistics(
        self,
        statistics: ApplicationStatistics | None,
        error: str = "",
    ) -> None:
        """Refresh the restored statistics view without coupling it to adapters."""

        self.statistics_page.set_statistics(statistics, error)

    @staticmethod
    def _effective_mode(theme: ClientTheme) -> str:
        if theme is ClientTheme.DARK:
            return "dark"
        if theme is ClientTheme.LIGHT:
            return "light"
        app = QApplication.instance()
        if app is not None and app.palette().window().color().lightness() < 128:
            return "dark"
        return "light"

    def _build_appearance_page(self, model: ClientSettingsViewModel) -> QWidget:
        page = QWidget()
        layout = _page_layout(page, spacing=10)
        layout.addWidget(_heading("Aussehen"))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        mode_label = _caption("Modus", wrap=False)
        self.sync_page.theme = ClickActivatedComboBox()
        self.sync_page.theme.addItem("System", ClientTheme.SYSTEM.value)
        self.sync_page.theme.addItem("Hell", ClientTheme.LIGHT.value)
        self.sync_page.theme.addItem("Dunkel", ClientTheme.DARK.value)
        self.sync_page.theme.setCurrentIndex(max(0, self.sync_page.theme.findData(model.theme)))
        form.addRow(mode_label, self.sync_page.theme)

        accent_label = _caption("Akzent", wrap=False)
        self.accent_combo = ClickActivatedComboBox()
        for label, color in (
            ("Mint", "#2db89d"),
            ("Ocean", "#1f7dbf"),
            ("Sunset", "#f08a3c"),
            ("Berry", "#c1457b"),
            ("Forest", "#2f9b61"),
            ("Eigene Farbe", "custom"),
        ):
            self.accent_combo.addItem(label, color)
        form.addRow(accent_label, self.accent_combo)
        layout.addLayout(form)

        chooser = QHBoxLayout()
        self.preview = QFrame()
        self.preview.setObjectName("AccentPreview")
        self.preview.setFixedSize(44, 26)
        chooser.addWidget(self.preview)
        self.pick_button = AppButton("Farbe wählen", AppButton.SECONDARY)
        self.pick_button.clicked.connect(self._pick_custom_color)
        chooser.addWidget(self.pick_button)
        chooser.addStretch()
        layout.addLayout(chooser)

        contrast_row = QHBoxLayout()
        contrast_label = _caption("Kontrast", wrap=False)
        contrast_row.addWidget(contrast_label)
        self.contrast_slider = ClickActivatedSlider(Qt.Orientation.Horizontal)
        self.contrast_slider.setObjectName("AppearanceSlider")
        self.contrast_slider.setRange(70, 140)
        self.contrast_slider.setValue(self._theme_manager.contrast)
        contrast_row.addWidget(self.contrast_slider, 1)
        self.contrast_value_label = QLabel(f"{self._theme_manager.contrast} %")
        self.contrast_value_label.setObjectName("SliderValue")
        self.contrast_value_label.setMinimumWidth(52)
        self.contrast_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        contrast_row.addWidget(self.contrast_value_label)
        layout.addLayout(contrast_row)

        font_row = QHBoxLayout()
        font_label = _caption("Schriftgröße", wrap=False)
        font_row.addWidget(font_label)
        self.font_size_slider = ClickActivatedSlider(Qt.Orientation.Horizontal)
        self.font_size_slider.setObjectName("AppearanceSlider")
        self.font_size_slider.setRange(10, 20)
        self.font_size_slider.setValue(self._theme_manager.font_size)
        font_row.addWidget(self.font_size_slider, 1)
        self.font_size_value_label = QLabel(f"{self._theme_manager.font_size} px")
        self.font_size_value_label.setObjectName("SliderValue")
        self.font_size_value_label.setMinimumWidth(52)
        self.font_size_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        font_row.addWidget(self.font_size_value_label)
        layout.addLayout(font_row)
        layout.addWidget(_caption("Bedienelemente und Textbereiche wachsen mit der Schriftgröße mit."))
        layout.addStretch()

        self._select_accent(self._theme_manager.accent)
        self._update_appearance_preview()
        self.accent_combo.currentIndexChanged.connect(self._appearance_changed)
        self.contrast_slider.valueChanged.connect(self._appearance_changed)
        self.font_size_slider.valueChanged.connect(self._appearance_changed)
        self.sync_page.theme.currentIndexChanged.connect(self._appearance_changed)
        if "theme" in model.environment_overrides:
            self.sync_page.theme.setEnabled(False)
            self.sync_page.theme.setToolTip("Die Darstellung wird durch PAPAGUI_CLIENT_THEME überschrieben.")
        for help_id, widgets in (
            ("appearance_mode", (mode_label, self.sync_page.theme)),
            ("appearance_accent", (accent_label, self.accent_combo, self.pick_button)),
            ("appearance_contrast", (contrast_label, self.contrast_slider)),
            ("appearance_font_size", (font_label, self.font_size_slider)),
        ):
            self.help_controller.register(help_id, SETTINGS_HELP_TEXTS[help_id], *widgets)
        return page

    def _show_page(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
        self.help_controller.hide()

    def _select_accent(self, accent: str) -> None:
        normalized = QColor(accent).name() if QColor(accent).isValid() else "#2db89d"
        index = self.accent_combo.findData(normalized)
        self.accent_combo.setCurrentIndex(index if index >= 0 else self.accent_combo.count() - 1)
        self._selected_accent = normalized

    def _appearance_changed(self, *_args) -> None:
        selected = self.accent_combo.currentData()
        if selected and selected != "custom":
            self._selected_accent = str(selected)
        self.pick_button.setEnabled(self.accent_combo.currentData() == "custom")
        self.contrast_value_label.setText(f"{self.contrast_slider.value()} %")
        self.font_size_value_label.setText(f"{self.font_size_slider.value()} px")
        self._update_appearance_preview()

    def _update_appearance_preview(self) -> None:
        self.preview.setStyleSheet(
            f"background-color: {self._selected_accent}; "
            "border: 1px solid #91a0a9; border-radius: 6px;"
        )

    def _pick_custom_color(self) -> None:
        self._color_dialog_active = True
        try:
            color = QColorDialog.getColor(
                QColor(self._selected_accent),
                self,
                "Akzentfarbe wählen",
                QColorDialog.ColorDialogOption.DontUseNativeDialog,
            )
            if color.isValid():
                self._selected_accent = color.name()
                self.accent_combo.setCurrentIndex(self.accent_combo.count() - 1)
                self._update_appearance_preview()
        finally:
            self._color_dialog_active = False

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

    def appearance_settings(self) -> AppearanceSettings:
        selected_theme = ClientTheme(str(self.sync_page.theme.currentData()))
        mode = self._effective_mode(selected_theme) if selected_theme is ClientTheme.SYSTEM else selected_theme.value
        return AppearanceSettings(
            mode, self._selected_accent, self.contrast_slider.value(), self.font_size_slider.value()
        )

    def _validate_and_accept(self) -> None:
        try:
            self.settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Einstellungen prüfen", str(exc))
            return
        self.accept()

    def size_for_parent(self) -> QSize:
        parent = self.parentWidget()
        if parent is None:
            return QSize(820, 600)
        return QSize(
            min(820, max(640, parent.width() - 48)),
            min(600, max(480, parent.height() - 48)),
        )

    def showEvent(self, event) -> None:
        self.resize(self.size_for_parent())
        parent = self.parentWidget()
        if parent is not None:
            center = parent.frameGeometry().center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            self.move(frame.topLeft())
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        if self._color_dialog_active:
            event.ignore()
            return
        self.help_controller.dispose()
        super().closeEvent(event)


class _DialogButtonsFacade:
    """Compatibility handle for callers that kept ``dialog.buttons``."""

    def __init__(self, save_button: AppButton, cancel_button: AppButton) -> None:
        self.save_button = save_button
        self.cancel_button = cancel_button


DARK_STYLE = """
QWidget { background: #17242d; color: #dceaf0; }
QLineEdit, QComboBox, QSpinBox, QTableWidget { background: #223540; color: #f1f7f9; border: 1px solid #486575; }
QTabWidget::pane { border-color: #486575; background: #17242d; }
QTabBar::tab { background: #29404c; color: #dceaf0; }
QTabBar::tab:selected { background: #17242d; color: #4ed8c4; }
"""


def theme_stylesheet(theme: ClientTheme | str, base_style: str) -> str:
    """Compatibility helper retained for the existing composition root."""

    selected = ClientTheme(theme)
    return base_style + DARK_STYLE if selected is ClientTheme.DARK else base_style
