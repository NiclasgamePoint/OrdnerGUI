"""Independent, v0.4.1-styled control center for the remote index server.

Only client-side presenters and gateways are used here.  The window deliberately
does not know whether the server is running in Docker, on a NAS, or elsewhere.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
from pathlib import Path
import sys
import time

from PySide6.QtCore import QDateTime, QIODevice, QLockFile, QSignalBlocker, QStandardPaths, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from papagui_client.composition import ClientContainer
from papagui_client.presentation.document_workers import document_worker_summary
from papagui_client.presentation.server_settings import ServerSettingsPresenter
from papagui_client.presentation.tray import TrayHealth, TrayPresenter, TrayStatusViewModel

from .icons import application_icon, set_process_identity
from .recognition_admin import RecognitionAdminWidget
from .recognition_review import RecognitionReviewDialog
from .tasks import BackgroundTask
from .theme import ThemeManager, ThemeSynchronizer, build_stylesheet
from .widgets.click_activated_inputs import ClickActivatedComboBox, ClickActivatedSpinBox


INSTANCE_NAME = "papagui-client-server-tray-v2"

RECOGNITION_BUDGET_FIELDS = (
    ("recognition_documents_per_project_max", "Prüfbare Dokumente je Projekt", 1, 500, ""),
    ("extraction_timeout_seconds", "Zeitlimit je Dokument", 1, 900, " s"),
    ("extraction_memory_mb", "Speicher je Dokumentleser", 128, 2048, " MiB"),
    ("pdf_max_pages", "Maximale PDF-Seiten je Dokument", 1, 2000, ""),
    ("image_max_pixels", "Maximale Bildgröße", 1_000_000, 100_000_000, " Pixel"),
    ("extraction_retry_attempts", "Wiederholungsversuche bei Lesefehlern", 1, 10, ""),
    ("extraction_retry_delay_seconds", "Pause vor erneutem Lesen", 1, 86_400, " s"),
    ("extraction_store_max_mb", "Speicher für Dokumentauszüge", 16, 102_400, " MB"),
    ("extraction_retention_days", "Aufbewahrung der Dokumentauszüge", 1, 365, " Tage"),
)


class ServerStatusBadge(QLabel):
    """The coloured v0.4.1 server badge, including its running animation."""

    _FRAMES = ("◐", "◓", "◑", "◒")
    _COLORS = {
        "online": "#27a989",
        "offline": "#d95c5c",
        "problem": "#df9328",
        "indexing": "#27a989",
        "connecting": "#718096",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "connecting"
        self._text = "VERBINDE …"
        self._frame = 0
        self.setObjectName("ServerStatusBadge")
        self.set_state("connecting", self._text)

    def set_state(self, state: str, text: str) -> None:
        self._state = state
        self._text = text
        self._frame = 0
        self.setProperty("status", state)
        self.style().unpolish(self)
        self.style().polish(self)
        self._render()

    def advance(self) -> None:
        if self._state != "indexing":
            return
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self._render()

    def _render(self) -> None:
        marker = self._FRAMES[self._frame] if self._state == "indexing" else "●"
        color = self._COLORS.get(self._state, self._COLORS["connecting"])
        self.setText(f'<span style="color:{color}">{marker}</span> {self._text}')


class ServerTrayWindow(QDialog):
    """Tool-window control center which talks exclusively to the server API."""

    _ACTIVE_STATES = frozenset({"starting", "running", "cancelling", "ready"})

    def __init__(self, container: ClientContainer):
        super().__init__()
        self._container = container
        self._pool = QThreadPool(self)
        self._tasks: set[BackgroundTask] = set()
        self._presenter = TrayPresenter()
        self._settings_presenter = ServerSettingsPresenter()
        self._settings: dict[str, object] = {}
        self._settings_dirty = False
        self._settings_busy = False
        self._settings_loaded = False
        self._applying_settings = False
        self._last_activity_key: tuple[str, ...] | None = None
        self._refreshing = False
        self._shutting_down = False

        self.setObjectName("IndexControlWindow")
        self.setWindowTitle("PapaGUI · Indexserver")
        self.setWindowIcon(application_icon("server"))
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setMinimumSize(760, 620)
        self.resize(900, 760)
        self._timer = QTimer(self)
        self._timer.setInterval(2_000)
        self._timer.timeout.connect(self.refresh_status)
        # Compatibility with the old window and existing lifecycle tests.
        self.timer = self._timer
        self._animation = QTimer(self)
        self._animation.setInterval(160)
        self._animation.timeout.connect(self._animate_running_badge)
        self._build()
        self._timer.start()
        QTimer.singleShot(0, self.refresh_status)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Indexserver")
        title.setObjectName("IndexControlTitle")
        heading.addWidget(title)
        caption = QLabel("Docker-Dienst, Indexjobs und Generationen zentral verwalten")
        caption.setObjectName("PopupCaption")
        heading.addWidget(caption)
        header.addLayout(heading, 1)
        self.server_badge = ServerStatusBadge(self)
        # ``state`` was the public badge attribute in the first 0.4.2 UI.
        self.state = self.server_badge
        header.addWidget(self.server_badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("IndexControlTabs")
        # Theme selector + explicit position preserve the requested clearance
        # from the rounded upper-left page corner on all Qt platform styles.
        self.tabs.setStyleSheet("QTabWidget#IndexControlTabs::tab-bar { left: 14px; }")
        self.tabs.addTab(self._overview(), "Übersicht")
        self.tabs.addTab(self._settings_page(), "Indexeinstellungen")
        self.recognition_admin_page = RecognitionAdminWidget(self._container.server_control)
        self.recognition_admin_page.reload_button.hide()
        self.tabs.addTab(self.recognition_admin_page, "Kundenerkennung")
        self.tabs.addTab(self._activity_page(), "Aktivität")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        self.last_refresh_label = QLabel("Noch nicht aktualisiert")
        self.last_refresh_label.setObjectName("PopupCaption")
        footer.addWidget(self.last_refresh_label)
        footer.addStretch()
        refresh_button = QPushButton("Aktualisieren")
        refresh_button.setProperty("buttonRole", "secondary")
        refresh_button.clicked.connect(self._refresh_current_page)
        footer.addWidget(refresh_button)
        close_button = QPushButton("Schließen")
        close_button.setProperty("buttonRole", "primary")
        close_button.clicked.connect(self.hide)
        footer.addWidget(close_button)
        root.addLayout(footer)

        self._apply_view_model(self._presenter.offline("Status wird geladen"))

    @staticmethod
    def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("IndexControlCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        label = QLabel(title)
        label.setObjectName("IndexCardTitle")
        layout.addWidget(label)
        return card, layout

    def _overview(self) -> QWidget:
        scroll = QScrollArea()
        page = QWidget()
        page.setObjectName("ThemedScrollContent")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 12)
        for title, builder in (
            ("Server", self._server_card),
            ("Aktueller Indexjob", self._job_card),
            ("Dokumentinhalte", self._content_card),
            ("Veröffentlichte Generation", self._generation_card),
        ):
            card, card_layout = self._card(title)
            builder(card_layout)
            layout.addWidget(card)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def _server_card(self, layout: QVBoxLayout) -> None:
        self.server_status_label = QLabel("Serverstatus wird geladen …")
        self.server_status_label.setObjectName("IndexStatusText")
        self.summary = self.server_status_label
        self.server_detail_label = QLabel("")
        self.server_detail_label.setObjectName("PopupCaption")
        self.server_detail_label.setWordWrap(True)
        self.server_details = self.server_detail_label
        layout.addWidget(self.server_status_label)
        layout.addWidget(self.server_detail_label)
        row = QHBoxLayout()
        self.restart_container_button = QPushButton("Container neu starten")
        self.restart_container_button.setProperty("buttonRole", "secondary")
        self.restart_container_button.clicked.connect(self._confirm_restart)
        row.addWidget(self.restart_container_button)
        self.review_button = QPushButton("Kundenerkennung prüfen")
        self.review_button.setProperty("buttonRole", "secondary")
        self.review_button.clicked.connect(self.open_recognition_review)
        row.addWidget(self.review_button)
        row.addStretch()
        layout.addLayout(row)


    def _job_card(self, layout: QVBoxLayout) -> None:
        self.status_label = QLabel("Noch keine Indexierung gestartet")
        self.status_label.setObjectName("IndexStatusText")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("PopupCaption")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.detail_label)
        row = QHBoxLayout()
        self.start_button = QPushButton("Jetzt aktualisieren")
        self.start_button.setProperty("buttonRole", "primary")
        self.start_button.clicked.connect(lambda: self.run_action("start"))
        row.addWidget(self.start_button)
        self.rebuild_button = QPushButton("Vollständig neu aufbauen")
        self.rebuild_button.setProperty("buttonRole", "secondary")
        self.rebuild_button.clicked.connect(self._confirm_rebuild)
        row.addWidget(self.rebuild_button)
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setProperty("buttonRole", "secondary")
        self.cancel_button.clicked.connect(lambda: self.run_action("cancel"))
        row.addWidget(self.cancel_button)
        row.addStretch()
        layout.addLayout(row)

    def _content_card(self, layout: QVBoxLayout) -> None:
        self.content_status_label = QLabel("Noch keine Dateiindizierung gestartet")
        self.content_status_label.setObjectName("IndexStatusText")
        self.content_progress_bar = QProgressBar()
        self.content_detail_label = QLabel("")
        self.content_detail_label.setObjectName("PopupCaption")
        self.content_detail_label.setWordWrap(True)
        self.worker_summary_label = QLabel("Keine aktiven Dokument-Worker")
        self.worker_summary_label.setObjectName("PopupCaption")
        self.worker_output = QPlainTextEdit()
        self.worker_output.setReadOnly(True)
        self.worker_output.setMaximumHeight(100)
        for widget in (
            self.content_status_label,
            self.content_progress_bar,
            self.content_detail_label,
            self.worker_summary_label,
            self.worker_output,
        ):
            layout.addWidget(widget)

    def _generation_card(self, layout: QVBoxLayout) -> None:
        self.generation_label = QLabel("Noch keine Indexgeneration verfügbar")
        self.generation_label.setObjectName("IndexStatusText")
        self.customer_generation_label = QLabel("Noch keine Kundengeneration verfügbar")
        self.customer_generation_label.setObjectName("IndexStatusText")
        self.backup_label = QLabel("Backups: –")
        self.backup_label.setObjectName("PopupCaption")
        self.retention_label = QLabel("")
        self.retention_label.setObjectName("PopupCaption")
        layout.addWidget(self.generation_label)
        layout.addWidget(self.customer_generation_label)
        layout.addWidget(self.backup_label)
        layout.addWidget(self.retention_label)
        row = QHBoxLayout()
        self.delete_button = QPushButton("Serverindex löschen und neu aufbauen")
        self.delete_button.setProperty("buttonRole", "danger")
        self.delete_button.clicked.connect(self._confirm_delete)
        row.addWidget(self.delete_button)
        row.addStretch()
        layout.addLayout(row)

    def _settings_page(self) -> QWidget:
        scroll = QScrollArea()
        page = QWidget()
        page.setObjectName("ThemedScrollContent")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 12)
        card, layout = self._card("Serverseitige Indexkonfiguration")
        hint = QLabel("Diese Werte gelten im Docker-Container ab dem nächsten Indexlauf.")
        hint.setObjectName("PopupCaption")
        layout.addWidget(hint)
        form = QFormLayout()

        interval_row = QHBoxLayout()
        self.interval_value = self._spin(15, 2880)
        self.interval_unit = ClickActivatedComboBox()
        self.interval_unit.addItems(["Minuten", "Stunden"])
        interval_row.addWidget(self.interval_value, 1)
        interval_row.addWidget(self.interval_unit)
        form.addRow("Automatischer Lauf", interval_row)
        self.interval_value.valueChanged.connect(self._mark_settings_dirty)
        self.interval_unit.currentTextChanged.connect(self._update_interval_range)
        self.interval_unit.currentTextChanged.connect(self._mark_settings_dirty)

        self.automatic_runs_enabled = self._check(form, "Automatische Läufe aktiv")
        self.daily_reconciliation_enabled = self._check(form, "Täglicher Abgleich")
        self.content_indexing_enabled = self._check(form, "Dokumentinhalte indexieren")
        self.content_enabled = self.content_indexing_enabled
        self.resource_profile = ClickActivatedComboBox()
        for label, value in (
            ("Schonend", "gentle"),
            ("Ausgewogen", "balanced"),
            ("Schnell", "fast"),
        ):
            self.resource_profile.addItem(label, value)
        self.resource_profile.currentIndexChanged.connect(self._mark_settings_dirty)
        self.resource_profile.setToolTip(
            "Bestimmt das Budget für parallele Dokumentverarbeitung. Die Zahl der Worker "
            "wird aus den verfügbaren CPU-Kernen und dem freien RAM des Servers berechnet; "
            "Containergrenzen und das Speicherlimit je Dokument werden berücksichtigt. "
            "Die tatsächlich verwendete Grenze steht im Dokumentstatus."
        )
        form.addRow("Ressourcenprofil", self.resource_profile)

        self.max_file_size_mb = self._spin(1, 102_400, " MB")
        self.max_file_size = self.max_file_size_mb
        form.addRow("Maximale Dateigröße", self.max_file_size_mb)
        self.max_extracted_characters = self._spin(1, 2_000_000_000)
        self.max_characters = self.max_extracted_characters
        form.addRow("Maximale Extraktlänge", self.max_extracted_characters)
        self.content_extensions = self._line(form, "Dateiendungen")
        self.extensions = self.content_extensions
        self.excluded_folders = self._line(form, "Ausgeschlossene Ordner")
        self.preferred_document_patterns = self._line(form, "Wichtige Dateinamen")
        self.preferred_patterns = self.preferred_document_patterns
        self.priority_documents_per_project = self._spin(form=None, minimum=0, maximum=100_000)
        self.priority_documents = self.priority_documents_per_project
        form.addRow("Schnelle Dokumente je Projekt", self.priority_documents_per_project)
        self.newest_years_first = self._check(form, "Neueste Jahre zuerst")
        self.newest_first = self.newest_years_first
        self.minimum_customer_year = self._spin(form=None, minimum=1900, maximum=9999)
        form.addRow("Minimales Kundenjahr", self.minimum_customer_year)

        self.ocr_enabled = self._check(form, "OCR verwenden")
        self.ocr_max_pages = self._spin(form=None, minimum=1, maximum=100_000)
        self.ocr_pages = self.ocr_max_pages
        form.addRow("OCR-Seiten erste Stufe", self.ocr_max_pages)
        self.ocr_extended_max_pages = self._spin(form=None, minimum=1, maximum=100_000)
        self.ocr_extended_pages = self.ocr_extended_max_pages
        form.addRow("OCR-Seiten erweitert", self.ocr_extended_max_pages)
        self.ocr_extension_threshold = self._spin(
            form=None, minimum=0, maximum=2_000_000_000, suffix=" Zeichen"
        )
        self.ocr_threshold = self.ocr_extension_threshold
        form.addRow("OCR-Erweiterung unter", self.ocr_extension_threshold)
        self.ocr_timeout_seconds = self._spin(
            form=None, minimum=1, maximum=86_400, suffix=" s"
        )
        self.ocr_timeout = self.ocr_timeout_seconds
        form.addRow("OCR-Zeitlimit", self.ocr_timeout_seconds)
        self.pdf_text_timeout_seconds = self._spin(
            form=None, minimum=1, maximum=86_400, suffix=" s"
        )
        self.pdf_timeout = self.pdf_text_timeout_seconds
        form.addRow("PDF-Zeitlimit", self.pdf_text_timeout_seconds)
        self.recognition_pipeline_enabled = self._check(form, "Kundendaten aus Dokumenten erkennen")
        self.recognition_own_names = self._line(form, "Eigene Firma / eigene Namen")
        self.recognition_own_names.setPlaceholderText("Namen mit Komma trennen")
        self.recognition_own_names.setToolTip(
            "Eigene Firmen- und Personennamen helfen, den eigenen Briefkopf von Kundendaten zu unterscheiden."
        )
        for name, label, minimum, maximum, suffix in RECOGNITION_BUDGET_FIELDS:
            control = self._spin(minimum, maximum, suffix)
            setattr(self, name, control)
            form.addRow(label, control)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.settings_load_button = QPushButton("Vom Server laden")
        self.settings_load_button.setProperty("buttonRole", "secondary")
        self.settings_load_button.clicked.connect(self.load_settings)
        row.addWidget(self.settings_load_button)
        row.addStretch()
        self.settings_save_button = QPushButton("Servereinstellungen speichern")
        self.settings_save_button.setProperty("buttonRole", "primary")
        self.settings_save_button.clicked.connect(self.save_settings)
        self.save_settings_button = self.settings_save_button
        row.addWidget(self.settings_save_button)
        layout.addLayout(row)
        self.settings_status = QLabel("Noch nicht vom Server geladen")
        self.settings_status.setObjectName("PopupCaption")
        layout.addWidget(self.settings_status)
        outer.addWidget(card)
        outer.addStretch()
        scroll.setWidget(page)
        return scroll

    def _spin(
        self,
        minimum: int,
        maximum: int,
        suffix: str = "",
        *,
        form: QFormLayout | None = None,
    ) -> QSpinBox:
        del form
        widget = ClickActivatedSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSuffix(suffix)
        widget.valueChanged.connect(self._mark_settings_dirty)
        return widget

    def _check(self, form: QFormLayout, label: str) -> QCheckBox:
        widget = QCheckBox()
        form.addRow(label, widget)
        widget.toggled.connect(self._mark_settings_dirty)
        return widget

    def _line(self, form: QFormLayout, label: str) -> QLineEdit:
        widget = QLineEdit()
        form.addRow(label, widget)
        widget.textChanged.connect(self._mark_settings_dirty)
        return widget

    def _activity_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 12)
        card, card_layout = self._card("Letzte lokale Client-Aktivität")
        self.activity = QListWidget()
        self.activity.setObjectName("IndexActivityLog")
        card_layout.addWidget(self.activity)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_output.setMaximumHeight(88)
        self.log_output.setPlaceholderText("Details zur ausgewählten Aktivität")
        self.activity.currentTextChanged.connect(self.log_output.setPlainText)
        card_layout.addWidget(self.log_output)
        layout.addWidget(card)
        return page

    def open_recognition_review(self) -> None:
        control = self._container.server_control
        if control is None:
            QMessageBox.warning(
                self,
                "Kundenerkennung",
                "Diese Clientinstanz besitzt keine Serversteuerung.",
            )
            return
        try:
            customers = self._container.customers.list()
        except Exception:
            customers = ()
        RecognitionReviewDialog(control, customers, self).exec()

    def open_recognition_admin(self) -> None:
        self.tabs.setCurrentWidget(self.recognition_admin_page)

    def _refresh_current_page(self) -> None:
        self.refresh_status()
        if self.tabs.currentWidget() is self.recognition_admin_page:
            self.recognition_admin_page.reload()

    def show_status(self) -> None:
        self.refresh_status()
        self._timer.start()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        """Compatibility alias retained for the v0.4.1 window API."""
        self.refresh_status()

    def refresh_status(self) -> None:
        if self._refreshing or self._shutting_down:
            return
        control = self._container.server_control
        if control is None:
            self._show_offline("Keine Serversteuerung konfiguriert")
            return
        self._refreshing = True
        task = BackgroundTask(control.status)
        task.signals.succeeded.connect(self._show_status)
        task.signals.failed.connect(self._show_offline)
        task.signals.finished.connect(self._refresh_finished)
        self._start_task(task)

    def _show_status(self, payload: object) -> None:
        values = payload if isinstance(payload, Mapping) else {}
        self._apply_view_model(self._presenter.status(values))
        self._apply_status_details(values)
        self._record_activity(values)

    def _show_offline(self, error: str) -> None:
        self._apply_view_model(self._presenter.offline(error))
        self.server_status_label.setText("Indexserver nicht erreichbar")
        self.server_detail_label.setText(error)
        self.last_refresh_label.setText("Server nicht erreichbar")

    def _apply_view_model(self, model: TrayStatusViewModel) -> None:
        if model.health is TrayHealth.OFFLINE:
            state, label = "offline", "OFFLINE"
        elif model.health is TrayHealth.PROBLEM:
            state, label = "problem", "PROBLEM"
        elif model.running:
            state, label = "indexing", "INDEXLAUF"
        else:
            state, label = "online", "ONLINE"
        self.server_badge.set_state(state, label)
        self.server_status_label.setText(model.summary)
        self.server_detail_label.setText(model.details)
        if model.running and not self._animation.isActive():
            self._animation.start()
        elif not model.running:
            self._animation.stop()

    def _apply_status_details(self, payload: Mapping[str, object]) -> None:
        raw_index = payload.get("index")
        index = raw_index if isinstance(raw_index, Mapping) else {}
        raw_progress = index.get("progress")
        progress = raw_progress if isinstance(raw_progress, Mapping) else {}
        state = str(index.get("state") or "idle").lower()
        phase = str(progress.get("phase") or index.get("phase") or "").strip()
        processed = self._integer(progress.get("processed_items"), 0)
        total = self._integer(progress.get("total_items"), 0)
        failed = self._integer(progress.get("failed_items"), 0)
        active = state in self._ACTIVE_STATES

        source_id = str(payload.get("source_id") or "–")
        source_available = payload.get("source_available")
        availability = (
            "verfügbar" if source_available is True else "nicht verfügbar"
            if source_available is False else "unbekannt"
        )
        uptime = self._format_duration(self._integer(payload.get("uptime_seconds"), 0))
        server_message = str(payload.get("message") or "").strip()
        raw_settings = payload.get("settings")
        status_settings = raw_settings if isinstance(raw_settings, Mapping) else {}
        details = [
            f"Serverversion: {payload.get('server_version') or '–'}",
            f"Datenquelle: {source_id} · {availability}",
            f"Laufzeit: {uptime}",
        ]
        if status_settings:
            interval = self._integer(status_settings.get("interval_seconds"), 86_400)
            automatic = bool(status_settings.get("automatic_runs_enabled", True))
            details.append(
                "Automatischer Lauf: "
                + (f"alle {self._format_interval(interval)}" if automatic else "deaktiviert")
            )
        if server_message:
            details.append(f"Hinweis: {server_message}")
        self.server_status_label.setText(
            "Docker-Indexdienst ist erreichbar"
            if str(payload.get("state") or "online").lower() == "online"
            else "Docker-Indexdienst meldet ein Problem"
        )
        self.server_detail_label.setText("\n".join(details))

        labels = {
            "starting": "Indexierung wird gestartet …",
            "running": "Indexierung läuft",
            "ready": "Index wird aktiviert …",
            "cancelling": "Indexierung wird abgebrochen …",
            "completed": "Indexjob erfolgreich abgeschlossen",
            "no_changes": "Index ist aktuell",
            "cancelled": "Indexierung wurde abgebrochen",
            "error": "Indexjob ist fehlgeschlagen",
            "failed": "Indexjob ist fehlgeschlagen",
            "idle": "Indexserver wartet",
        }
        phase_labels = {
            "catalog": "Dateien und Dokumentinhalte erfassen",
            "customer-recognition": "Kunden und Projekte zuordnen",
            "customer-documents": "Kundendaten aus Dokumenten prüfen",
            "publishing": "Index und Kundendaten veröffentlichen",
        }
        recognition_phase = phase in {"customer-recognition", "customer-documents"}
        after_scan = recognition_phase or phase == "publishing"
        # Older servers keep the completed file count during recognition.
        has_recognition_progress = "catalog_processed_items" in progress
        phase_processed = 0 if after_scan and not has_recognition_progress else processed
        self.status_label.setText(
            phase_labels[phase] + " …" if state == "running" and phase in phase_labels
            else labels.get(state, str(index.get("message") or state))
        )
        self._set_progress(self.progress_bar, phase_processed, total, active)
        job_details: list[str] = []
        if phase:
            job_details.append(f"Phase: {phase_labels.get(phase, phase)}")
        if after_scan:
            scanned = self._integer(progress.get("catalog_processed_items"), processed)
            job_details.append(f"Dateierfassung abgeschlossen: {scanned} Dateien")
        if recognition_phase and has_recognition_progress:
            unit = "Kundengruppen zugeordnet" if phase == "customer-recognition" else "Kunden geprüft"
            if total:
                job_details.append(f"{processed} von {total} {unit}")
            if phase == "customer-documents":
                documents = self._integer(progress.get("evaluated_documents"), 0)
                job_details.append(f"{documents} Dokumente auf Kundendaten geprüft")
        elif not after_scan and (processed or total):
            job_details.append(
                f"{processed} von {total} Einträgen verarbeitet" if total else f"{processed} Einträge verarbeitet"
            )
        if failed:
            job_details.append(f"{failed} Einträge mit Fehlern")
        current = self._current_source(progress)
        if current and not after_scan:
            job_details.append(f"Aktuell: {current}")
        message = str(index.get("message") or "").strip()
        if message:
            job_details.append(message)
        queued_action = str(payload.get("queued_action") or "").strip()
        if queued_action:
            job_details.append(f"Vorgemerkt: {queued_action}")
        if payload.get("resumable") is True:
            job_details.append("Ein unterbrochener Lauf kann fortgesetzt werden")
        self.detail_label.setText("\n".join(dict.fromkeys(job_details)))
        self.cancel_button.setVisible(active)
        self.start_button.setEnabled(not active)
        self.rebuild_button.setEnabled(not active)

        content_phase = not after_scan and any(
            token in phase.casefold()
            for token in ("catalog", "content", "document", "extract", "ocr", "inhalt", "dokument")
        )
        if active and content_phase:
            self.content_status_label.setText("Dokumentinhalte werden indexiert")
        elif after_scan or state in {"completed", "no_changes"}:
            self.content_status_label.setText("Dateiindizierung abgeschlossen")
        elif state in {"error", "failed"}:
            self.content_status_label.setText("Dateiindizierung mit Fehler beendet")
        else:
            self.content_status_label.setText("Dateiindizierung wartet")
        content_count = self._integer(progress.get("catalog_processed_items"), processed)
        self._set_progress(
            self.content_progress_bar, content_count, content_count if after_scan else total,
            active and content_phase,
        )
        self.content_detail_label.setText(
            " · ".join(
                part
                for part in (
                    "Dateierfassung abgeschlossen" if after_scan else f"Phase {phase}" if phase else "",
                    f"{content_count} Dateien" if after_scan else f"{processed} / {total}" if total else "",
                    f"{failed} Fehler" if failed else "",
                )
                if part
            )
            or "Noch keine Dokumentstatistik verfügbar"
        )
        self.worker_summary_label.setText(
            "Dieser Server meldet keine Dokument-Worker-Statistik"
        )
        self.worker_output.clear()
        raw_workers = payload.get("document_workers")
        if isinstance(raw_workers, Mapping):
            self._apply_document_workers(raw_workers)

        index_generation = str(payload.get("active_index_generation") or "")
        customer_generation = str(payload.get("active_customer_generation") or "")
        self.generation_label.setText(
            f"Indexgeneration {index_generation}"
            if index_generation
            else "Noch keine Indexgeneration verfügbar"
        )
        self.customer_generation_label.setText(
            f"Kundengeneration {customer_generation}"
            if customer_generation
            else "Noch keine Kundengeneration verfügbar"
        )
        raw_backups = payload.get("backups")
        if isinstance(raw_backups, Mapping):
            index_backups = self._integer(raw_backups.get("index"), 0)
            customer_backups = self._integer(raw_backups.get("customers"), 0)
            self.backup_label.setText(
                f"Backups auf dem Server: Index {index_backups} von 3 · Kunden {customer_backups} von 3"
            )
        else:
            self.backup_label.setText(f"Backups auf dem Server: {self._integer(raw_backups, 0)} von 3")
        raw_retention = payload.get("retention")
        retention = raw_retention if isinstance(raw_retention, Mapping) else {}
        retention_state = str(retention.get("state") or "").strip()
        failures = retention.get("failures")
        self.retention_label.setText(
            "Aufbewahrung: "
            + ("vollständig" if retention_state in {"", "ok"} else retention_state)
            + (f" · {failures}" if failures else "")
        )

        if isinstance(raw_settings, Mapping) and not self._settings_dirty:
            self._apply_settings({"settings": raw_settings}, saved=False)
            self._settings_loaded = True
        self.last_refresh_label.setText(
            "Live-Status aktualisiert · "
            + QDateTime.currentDateTime().toString("dd.MM.yyyy HH:mm:ss")
        )

    def _apply_document_workers(self, workers: Mapping[str, object]) -> None:
        state = str(workers.get("state", "idle"))
        active = state in {"running", "cancelling"}
        labels = {
            "idle": "Dokumentverarbeitung wartet",
            "running": "Dokumentinhalte werden verarbeitet",
            "cancelling": "Dokumentverarbeitung wird abgebrochen …",
            "completed": "Dokumentverarbeitung abgeschlossen",
            "cancelled": "Dokumentverarbeitung abgebrochen",
            "error": "Dokumentverarbeitung mit Fehler beendet",
        }
        self.content_status_label.setText(labels.get(state, "Dokumentverarbeitung"))
        summary, details = document_worker_summary(workers)
        self.worker_summary_label.setText(summary)
        self.content_detail_label.setText(details)
        # Discovery continues during extraction, so the final total is not yet known.
        self._set_progress(
            self.content_progress_bar,
            self._integer(workers.get("processed_documents"), 0),
            0 if active else self._integer(workers.get("discovered_documents"), 0),
            active,
        )
        elapsed = self._format_duration(self._integer(workers.get("elapsed_seconds"), 0))
        mode = "Inhaltsprüfung" if workers.get("verify_content") else "Schneller Abgleich"
        self.worker_output.setPlainText(f"{mode} · Laufzeit: {elapsed}")

    @staticmethod
    def _integer(value: object, default: int) -> int:
        if isinstance(value, bool):
            return default
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_duration(seconds: int) -> str:
        days, remainder = divmod(max(0, seconds), 86_400)
        hours, remainder = divmod(remainder, 3_600)
        minutes, remaining_seconds = divmod(remainder, 60)
        if days:
            return f"{days} d {hours:02d}:{minutes:02d}:{remaining_seconds:02d}"
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"

    @staticmethod
    def _format_interval(seconds: int) -> str:
        seconds = max(900, min(172_800, seconds))
        if seconds % 3_600 == 0:
            hours = seconds // 3_600
            return f"{hours} Stunde" if hours == 1 else f"{hours} Stunden"
        return f"{max(15, seconds // 60)} Minuten"

    @staticmethod
    def _current_source(progress: Mapping[str, object]) -> str:
        raw = progress.get("current_source")
        if isinstance(raw, Mapping):
            source_id = str(raw.get("source_id") or "")
            relative = str(raw.get("relative_path") or "")
            if source_id or relative:
                return f"{source_id}:{relative}".strip(":")
        return str(progress.get("legacy_current_path") or progress.get("current_path") or "")

    @staticmethod
    def _set_progress(bar: QProgressBar, processed: int, total: int, active: bool) -> None:
        if total > 0:
            bar.setRange(0, total)
            bar.setValue(min(processed, total))
            bar.setFormat(f"%v / {total}")
        elif active:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 100)
            bar.setValue(100 if processed else 0)
            bar.setFormat("%p %")

    def _animate_running_badge(self) -> None:
        self.server_badge.advance()

    def _refresh_finished(self) -> None:
        self._refreshing = False

    def run_action(self, action: str) -> None:
        control = self._container.server_control
        if control is None:
            self._show_action_error("Keine Serversteuerung konfiguriert")
            return
        self.activity.insertItem(0, f"Aktion angefordert: {action}")
        task = BackgroundTask(lambda: control.index_action(action))
        task.signals.succeeded.connect(self._action_complete)
        task.signals.failed.connect(self._show_action_error)
        self._start_task(task)

    def _confirm_rebuild(self) -> None:
        if QMessageBox.question(
            self,
            "Index neu aufbauen",
            "Kompletten Serverindex neu aufbauen?",
        ) == QMessageBox.StandardButton.Yes:
            self.run_action("full_rebuild")

    def _confirm_restart(self) -> None:
        if QMessageBox.question(
            self,
            "Container neu starten",
            "Den Docker-Indexdienst kontrolliert beenden und neu starten? "
            "Ein laufender Indexjob wird vorher abgebrochen.",
        ) == QMessageBox.StandardButton.Yes:
            self.run_action("restart_server")

    def _confirm_delete(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Serverindex löschen",
            "Index und Generationen löschen und anschließend neu aufbauen? "
            "customers.db bleibt erhalten.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.run_action("delete")

    def load_settings(self, _checked: bool = False, *, force: bool = False) -> None:
        if self._settings_busy:
            return
        if self._settings_dirty and not force:
            answer = QMessageBox.question(
                self,
                "Ungespeicherte Änderungen",
                "Lokale Änderungen verwerfen und Werte neu vom Server laden?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._set_settings_busy(True, "Servereinstellungen werden geladen …")
        task = BackgroundTask(self._container.server_control.settings)
        task.signals.succeeded.connect(
            lambda response: self._apply_settings(response, saved=False)
        )
        task.signals.failed.connect(self._settings_failed)
        task.signals.finished.connect(self._settings_finished)
        self._start_task(task)

    def _apply_settings(self, response: object, *, saved: bool = False) -> None:
        if not isinstance(response, Mapping):
            self._settings_failed("Servereinstellungen müssen ein Objekt sein")
            return
        model = self._settings_presenter.present(response)
        self._settings = dict(model.values)
        self._applying_settings = True
        try:
            self.interval_unit.setCurrentText(model.interval_unit)
            self.interval_value.setValue(model.interval_value)
            for name in (
                "automatic_runs_enabled",
                "daily_reconciliation_enabled",
                "content_indexing_enabled",
                "ocr_enabled",
                "newest_years_first",
                "recognition_pipeline_enabled",
            ):
                getattr(self, name).setChecked(bool(model.values[name]))
            for name in (
                "max_file_size_mb",
                "max_extracted_characters",
                "ocr_max_pages",
                "ocr_extended_max_pages",
                "ocr_extension_threshold",
                "ocr_timeout_seconds",
                "pdf_text_timeout_seconds",
                "priority_documents_per_project",
                "minimum_customer_year",
                *(field[0] for field in RECOGNITION_BUDGET_FIELDS),
            ):
                getattr(self, name).setValue(int(model.values[name]))
            for name in (
                "content_extensions",
                "excluded_folders",
                "preferred_document_patterns",
                "recognition_own_names",
            ):
                getattr(self, name).setText(str(model.values[name]))
            profile_index = self.resource_profile.findData(
                str(model.values["resource_profile"])
            )
            self.resource_profile.setCurrentIndex(max(0, profile_index))
        finally:
            self._applying_settings = False
        self._settings_dirty = False
        self._settings_loaded = True
        self.settings_status.setText("Gespeichert" if saved else "Vom Server geladen")
        self.settings_save_button.setEnabled(False)

    def save_settings(self) -> None:
        if self._settings_busy:
            return
        try:
            settings = self._collect_settings()
        except ValueError as exc:
            self._settings_failed(str(exc))
            return
        self._set_settings_busy(True, "Servereinstellungen werden gespeichert …")
        task = BackgroundTask(
            lambda: self._container.server_control.save_settings(settings)
        )
        task.signals.succeeded.connect(
            lambda response: self._apply_settings(response, saved=True)
        )
        task.signals.failed.connect(self._settings_failed)
        task.signals.finished.connect(self._settings_finished)
        self._start_task(task)

    def _collect_settings(self) -> dict[str, object]:
        values = {
            "automatic_runs_enabled": self.automatic_runs_enabled.isChecked(),
            "interval_seconds": self._settings_presenter.interval_seconds(
                self.interval_value.value(), self.interval_unit.currentText()
            ),
            "daily_reconciliation_enabled": self.daily_reconciliation_enabled.isChecked(),
            "content_indexing_enabled": self.content_indexing_enabled.isChecked(),
            "content_extensions": self.content_extensions.text().strip(),
            "excluded_folders": self.excluded_folders.text().strip(),
            "max_file_size_mb": self.max_file_size_mb.value(),
            "max_extracted_characters": self.max_extracted_characters.value(),
            "ocr_enabled": self.ocr_enabled.isChecked(),
            "ocr_max_pages": self.ocr_max_pages.value(),
            "ocr_extended_max_pages": self.ocr_extended_max_pages.value(),
            "ocr_extension_threshold": self.ocr_extension_threshold.value(),
            "ocr_timeout_seconds": self.ocr_timeout_seconds.value(),
            "pdf_text_timeout_seconds": self.pdf_text_timeout_seconds.value(),
            "resource_profile": self.resource_profile.currentData(),
            "preferred_document_patterns": self.preferred_document_patterns.text().strip(),
            "priority_documents_per_project": self.priority_documents_per_project.value(),
            "newest_years_first": self.newest_years_first.isChecked(),
            "minimum_customer_year": self.minimum_customer_year.value(),
            "recognition_pipeline_enabled": self.recognition_pipeline_enabled.isChecked(),
            "recognition_own_names": self.recognition_own_names.text().strip(),
            **{field[0]: getattr(self, field[0]).value() for field in RECOGNITION_BUDGET_FIELDS},
        }
        return self._settings_presenter.validate(values)

    def _mark_settings_dirty(self, *_args: object) -> None:
        if self._applying_settings:
            return
        self._settings_dirty = True
        self.settings_status.setText("Ungespeicherte Änderungen")
        self.settings_save_button.setEnabled(not self._settings_busy)

    def _set_settings_busy(self, busy: bool, message: str = "") -> None:
        self._settings_busy = busy
        self.settings_load_button.setEnabled(not busy)
        self.settings_save_button.setEnabled(not busy and self._settings_dirty)
        if message:
            self.settings_status.setText(message)

    def _settings_finished(self) -> None:
        self._set_settings_busy(False)

    def _settings_failed(self, error: str) -> None:
        self.settings_status.setText(f"Fehler: {error}")
        QMessageBox.warning(self, "Servereinstellungen", error)

    def _update_interval_range(self, unit: str) -> None:
        old_seconds = self.interval_value.value() * (
            3_600 if unit == "Minuten" else 60
        )
        minimum, maximum = ((1, 48) if unit == "Stunden" else (15, 2_880))
        converted = max(
            minimum,
            min(maximum, round(old_seconds / (3_600 if unit == "Stunden" else 60))),
        )
        with QSignalBlocker(self.interval_value):
            self.interval_value.setRange(minimum, maximum)
            self.interval_value.setValue(converted)

    def _record_activity(self, payload: Mapping[str, object]) -> None:
        model = self._presenter.activity(payload)
        if model.key == self._last_activity_key:
            return
        self._last_activity_key = model.key
        self.activity.insertItem(0, model.text)
        while self.activity.count() > 50:
            self.activity.takeItem(self.activity.count() - 1)

    def _show_action_error(self, error: str) -> None:
        self.activity.insertItem(0, f"Fehler: {error}")
        QMessageBox.warning(self, "Serveraktion fehlgeschlagen", error)

    def _action_complete(self, _value: object) -> None:
        self.last_refresh_label.setText("Aktion vom Server angenommen")
        self.refresh_status()

    def _start_task(self, task: BackgroundTask) -> None:
        self._tasks.add(task)
        task.signals.finished.connect(self._task_finished)
        self._pool.start(task)

    def _task_finished(self) -> None:
        sender = self.sender()
        self._tasks = {task for task in self._tasks if task.signals is not sender}

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._shutting_down:
            event.accept()
            return
        self.hide()
        event.ignore()

    def shutdown(self) -> None:
        self._shutting_down = True
        self.recognition_admin_page.shutdown()
        self._timer.stop()
        self._animation.stop()
        self._pool.clear()
        for task in tuple(self._tasks):
            signals = task.signals
            individual_signals = [
                getattr(signals, name, None)
                for name in ("succeeded", "failed", "finished")
            ]
            if any(signal is not None for signal in individual_signals):
                for signal in individual_signals:
                    if signal is None:
                        continue
                    try:
                        signal.disconnect()
                    except (RuntimeError, TypeError):
                        pass
            else:
                try:
                    signals.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self._pool.waitForDone(1_000)


class TrayController:
    def __init__(self, application: QApplication, container: ClientContainer, show: bool):
        self.application = application
        self.window = ServerTrayWindow(container)
        self.icon = QSystemTrayIcon(
            self.window.windowIcon(),
            application,
        )
        menu = QMenu()
        open_action = QAction("Indexserver öffnen", menu)
        open_action.triggered.connect(self.show)
        menu.addAction(open_action)
        menu.addSeparator()
        quit_action = QAction("Indextray beenden", menu)
        quit_action.triggered.connect(application.quit)
        menu.addAction(quit_action)
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        self.icon.setToolTip("PapaGUI Indexserver")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.icon.show()
        if show:
            self.show()
        application.aboutToQuit.connect(self.shutdown)

    def show(self) -> None:
        self.window.show_status()

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()

    def shutdown(self) -> None:
        self.icon.hide()
        self.window.shutdown()
        self.window.close()


class SingleInstanceServer:
    def __init__(self, lock_directory: Path | None = None):
        directory = lock_directory or Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        )
        directory.mkdir(parents=True, exist_ok=True)
        suffix = hashlib.sha256(str(directory.resolve()).encode("utf-8")).hexdigest()[:16]
        self.name = f"{INSTANCE_NAME}-{suffix}"
        self.lock = QLockFile(str(directory / "index-tray.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def claim(self, on_show, *, request_show: bool = True) -> bool:
        # Windows permits several QLocalServers to listen on the same named pipe.
        # Claim an OS-backed file lock before creating either a listener or a GUI.
        if not self.lock.tryLock(0):
            if self.lock.error() != QLockFile.LockError.LockFailedError:
                raise OSError("Die Sperre für den Indextray konnte nicht angelegt werden.")
            if request_show:
                self._request_show()
            return False
        if not self.server.listen(self.name):
            # Only the lock owner may clean up a Unix socket left by a crashed tray.
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                self.lock.unlock()
                raise OSError(self.server.errorString())
        self.server.newConnection.connect(lambda: self._receive(on_show))
        return True

    def _request_show(self) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.name, QIODevice.OpenModeFlag.WriteOnly)
            if socket.waitForConnected(100):
                socket.write(b"show")
                if socket.waitForBytesWritten(300):
                    socket.disconnectFromServer()
                    return
            socket.abort()
            time.sleep(0.05)
        raise OSError("Der Indextray antwortet noch nicht. Bitte erneut öffnen.")

    def close(self) -> None:
        self.server.close()
        self.lock.unlock()

    def _receive(self, on_show) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is not None:
                socket.waitForReadyRead(100)
                if bytes(socket.readAll()).strip() == b"show":
                    on_show()
                socket.deleteLater()


def run_tray_gui(container: ClientContainer, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--background", action="store_true")
    options = parser.parse_args(argv)
    set_process_identity("server")
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("PapaGUI Indexserver")
    application.setWindowIcon(application_icon("server"))
    application.setQuitOnLastWindowClosed(False)
    theme = ThemeManager()
    if hasattr(application, "setPalette"):
        theme.apply(application)
    else:
        application.setStyleSheet(
            build_stylesheet(theme.mode, theme.accent, theme.contrast, theme.font_size)
        )
    instance = SingleInstanceServer()
    controller = None
    show_requested = not options.background

    def show_existing() -> None:
        nonlocal show_requested
        show_requested = True
        if controller is not None:
            controller.show()

    if not instance.claim(show_existing, request_show=not options.background):
        return 0
    theme_sync = ThemeSynchronizer(application)
    try:
        theme_sync.start()
        controller = TrayController(application, container, show=False)
        from papagui_client.updates.runtime import signal_ready

        if show_requested:
            controller.show()
        QTimer.singleShot(0, signal_ready)
        application._papagui_tray = (controller, instance)  # type: ignore[attr-defined]
        return application.exec()
    finally:
        theme_sync.stop()
        instance.close()
