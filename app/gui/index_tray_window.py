from __future__ import annotations

from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from app.core.index_job_state import ACTIVE_STATUSES, read_state
from app.core.logging_config import LOG_FILE
from app.services.index_server_client import IndexServerClient, IndexServerError


class _ServerWorker(QThread):
    completed = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, client, operation, payload=None, parent=None):
        super().__init__(parent)
        self.client, self.operation, self.payload = client, operation, payload

    def run(self):
        try:
            if self.operation == "status":
                result = self.client.status()
            elif self.operation == "settings":
                result = self.client.settings()
            elif self.operation == "save_settings":
                result = self.client.save_settings(dict(self.payload or {}))
            else:
                result = self.client.action(self.operation)
        except IndexServerError as exc:
            self.failed.emit(self.operation, str(exc))
        else:
            self.completed.emit(self.operation, result)


class IndexTrayWindow(QDialog):
    """Control center for the remote Docker index service."""

    cancelRequested = Signal()

    def __init__(self, state_dir: Path, log_file: Path = LOG_FILE, parent=None,
                 content_state_dir: Path | None = None, server_url: str = "",
                 api_token: str = ""):
        super().__init__(parent)
        self.state_dir, self.content_state_dir, self.log_file = (
            state_dir, content_state_dir, log_file
        )
        self.server_client = IndexServerClient(server_url, api_token, 2) if server_url else None
        self._worker = None
        self._settings_loaded = False
        self.setObjectName("IndexControlWindow")
        self.setWindowTitle("PapaGUI · Indexserver")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setMinimumSize(760, 620)
        self.resize(900, 760)
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
        self.server_badge = QLabel("● VERBINDE …")
        self.server_badge.setObjectName("ServerStatusBadge")
        header.addWidget(self.server_badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._status_tab(), "Übersicht")
        self.tabs.addTab(self._settings_tab(), "Indexeinstellungen")
        self.tabs.addTab(self._log_tab(), "Aktivität")
        root.addWidget(self.tabs, 1)
        footer = QHBoxLayout()
        self.last_refresh_label = QLabel("Noch nicht aktualisiert")
        self.last_refresh_label.setObjectName("PopupCaption")
        footer.addWidget(self.last_refresh_label)
        footer.addStretch()
        for text, slot, role in (
            ("Aktualisieren", self.refresh, "secondary"),
            ("Schließen", self.hide, "primary"),
        ):
            button = QPushButton(text)
            button.setProperty("buttonRole", role)
            button.clicked.connect(slot)
            footer.addWidget(button)
        root.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(2_000)
        self.timer.timeout.connect(self.refresh)

    @staticmethod
    def _card(title):
        card = QFrame()
        card.setObjectName("IndexControlCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        label = QLabel(title)
        label.setObjectName("IndexCardTitle")
        layout.addWidget(label)
        return card, layout

    def _status_tab(self):
        scroll, page = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 12)
        for title, builder in (
            ("Server", self._server_card), ("Aktueller Indexjob", self._job_card),
            ("Dokumentinhalte", self._content_card),
            ("Veröffentlichte Generation", self._generation_card),
        ):
            card, card_layout = self._card(title)
            builder(card_layout)
            layout.addWidget(card)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def _server_card(self, layout):
        self.server_status_label = QLabel("Serverstatus wird geladen …")
        self.server_status_label.setObjectName("IndexStatusText")
        self.server_detail_label = QLabel("")
        self.server_detail_label.setObjectName("PopupCaption")
        self.server_detail_label.setWordWrap(True)
        layout.addWidget(self.server_status_label)
        layout.addWidget(self.server_detail_label)
        row = QHBoxLayout()
        self.restart_container_button = QPushButton("Container neu starten")
        self.restart_container_button.setProperty("buttonRole", "secondary")
        self.restart_container_button.clicked.connect(self._confirm_restart)
        row.addWidget(self.restart_container_button)
        row.addStretch()
        layout.addLayout(row)

    def _job_card(self, layout):
        self.status_label = QLabel("Noch keine Indexierung gestartet")
        self.status_label.setObjectName("IndexStatusText")
        self.progress_bar = QProgressBar()
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("PopupCaption")
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.detail_label)
        row = QHBoxLayout()
        actions = (
            ("Jetzt aktualisieren", lambda: self._action("run"), "primary"),
            ("Vollständig neu aufbauen", self._confirm_rebuild, "secondary"),
            ("Abbrechen", lambda: self._action("cancel"), "secondary"),
        )
        for text, slot, role in actions:
            button = QPushButton(text)
            button.setProperty("buttonRole", role)
            button.clicked.connect(slot)
            row.addWidget(button)
            if text == "Abbrechen":
                self.cancel_button = button
        row.addStretch()
        layout.addLayout(row)

    def _content_card(self, layout):
        self.content_status_label = QLabel("Noch keine Dateiindizierung gestartet")
        self.content_status_label.setObjectName("IndexStatusText")
        self.content_progress_bar = QProgressBar()
        self.content_detail_label = QLabel("")
        self.content_detail_label.setObjectName("PopupCaption")
        self.worker_summary_label = QLabel("Keine aktiven Dokument-Worker")
        self.worker_summary_label.setObjectName("PopupCaption")
        self.worker_output = QPlainTextEdit()
        self.worker_output.setReadOnly(True)
        self.worker_output.setMaximumHeight(100)
        for widget in (self.content_status_label, self.content_progress_bar,
                       self.content_detail_label, self.worker_summary_label,
                       self.worker_output):
            layout.addWidget(widget)

    def _generation_card(self, layout):
        self.generation_label = QLabel("Noch keine Generation verfügbar")
        self.generation_label.setObjectName("IndexStatusText")
        self.backup_label = QLabel("Backups: –")
        self.backup_label.setObjectName("PopupCaption")
        layout.addWidget(self.generation_label)
        layout.addWidget(self.backup_label)
        row = QHBoxLayout()
        self.delete_button = QPushButton("Serverindex löschen und neu aufbauen")
        self.delete_button.setProperty("buttonRole", "danger")
        self.delete_button.clicked.connect(self._confirm_delete)
        row.addWidget(self.delete_button)
        row.addStretch()
        layout.addLayout(row)

    def _settings_tab(self):
        scroll, page = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(page)
        card, layout = self._card("Serverseitige Indexkonfiguration")
        hint = QLabel("Diese Werte gelten im Docker-Container ab dem nächsten Indexlauf.")
        hint.setObjectName("PopupCaption")
        layout.addWidget(hint)
        form = QFormLayout()
        self.interval_spin = self._spin(5, 10080, " Minuten")
        form.addRow("Automatischer Lauf", self.interval_spin)
        self.content_enabled = QCheckBox("Dokumentinhalte indexieren")
        form.addRow("Inhaltssuche", self.content_enabled)
        self.resource_profile = QComboBox()
        for label, value in (("Schonend", "gentle"), ("Ausgewogen", "balanced"), ("Schnell", "fast")):
            self.resource_profile.addItem(label, value)
        form.addRow("Ressourcenprofil", self.resource_profile)
        fields = (
            ("max_file_size", "Maximale Dateigröße", self._spin(1, 10240, " MB")),
            ("max_characters", "Maximale Extraktlänge", self._spin(10000, 20000000)),
            ("extensions", "Dateiendungen", QLineEdit()),
            ("excluded_folders", "Ausgeschlossene Ordner", QLineEdit()),
            ("preferred_patterns", "Wichtige Dateinamen", QLineEdit()),
            ("priority_documents", "Schnelle Dokumente je Projekt", self._spin(1, 100)),
        )
        for name, label, widget in fields:
            setattr(self, name, widget)
            form.addRow(label, widget)
        self.newest_first = QCheckBox("Neueste Jahre zuerst")
        form.addRow("Reihenfolge", self.newest_first)
        self.ocr_enabled = QCheckBox("OCR verwenden")
        form.addRow("OCR", self.ocr_enabled)
        for name, label, widget in (
            ("ocr_pages", "OCR-Seiten erste Stufe", self._spin(1, 1000)),
            ("ocr_extended_pages", "OCR-Seiten erweitert", self._spin(1, 1000)),
            ("ocr_threshold", "OCR-Erweiterung unter", self._spin(0, 100000, " Zeichen")),
            ("ocr_timeout", "OCR-Zeitlimit", self._spin(5, 600, " s")),
            ("pdf_timeout", "PDF-Zeitlimit", self._spin(5, 600, " s")),
        ):
            setattr(self, name, widget)
            form.addRow(label, widget)
        layout.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        button = QPushButton("Servereinstellungen speichern")
        button.clicked.connect(self._save_settings)
        row.addWidget(button)
        layout.addLayout(row)
        outer.addWidget(card)
        outer.addStretch()
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _spin(minimum, maximum, suffix=""):
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSuffix(suffix)
        return widget

    def _log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        card, card_layout = self._card("Letzte lokale Client-Aktivität")
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        card_layout.addWidget(self.log_output)
        layout.addWidget(card)
        return page

    def show_status(self):
        self.refresh()
        if self.server_client and not self._settings_loaded:
            QTimer.singleShot(100, lambda: self._start("settings"))
        self.timer.start()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self):
        self._refresh_log()
        if self.server_client:
            self._start("status")
        else:
            self.server_badge.setText("● LOKAL")
            self._apply_job(read_state(self.state_dir))
            self._apply_content(read_state(self.content_state_dir) if self.content_state_dir else {})

    def _start(self, operation, payload=None):
        if not self.server_client or (self._worker and self._worker.isRunning()):
            return
        worker = _ServerWorker(self.server_client, operation, payload, self)
        worker.completed.connect(self._result)
        worker.failed.connect(self._error)
        worker.finished.connect(self._release_worker)
        self._worker = worker
        worker.start()

    def _release_worker(self):
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        worker.deleteLater()

    def _result(self, operation, payload):
        if operation == "status":
            self._apply_status(dict(payload))
        elif operation in {"settings", "save_settings"}:
            self._apply_settings(dict(payload))
            self._settings_loaded = True
            self.last_refresh_label.setText("Servereinstellungen gespeichert" if operation == "save_settings" else "Einstellungen geladen")
        else:
            self.last_refresh_label.setText("Aktion angenommen")
            QTimer.singleShot(400, self.refresh)

    def _error(self, operation, message):
        self.server_badge.setText("● OFFLINE")
        self.server_badge.setProperty("status", "offline")
        self.server_status_label.setText("Indexserver nicht erreichbar")
        self.server_detail_label.setText(message)
        if operation not in {"status", "settings"}:
            QMessageBox.warning(self, "Serveraktion fehlgeschlagen", message)

    def _apply_status(self, payload):
        server = dict(payload.get("server") or {})
        self.server_badge.setText("● ONLINE")
        self.server_badge.setProperty("status", "online")
        self.server_status_label.setText("Docker-Indexdienst ist erreichbar")
        interval = int(server.get("interval_seconds") or 0)
        self.server_detail_label.setText(f"Quelle: {server.get('source') or '–'}\nAutomatischer Lauf: alle {max(1, interval // 60)} Minuten")
        self.interval_spin.setValue(max(5, interval // 60))
        self._apply_job(dict(payload.get("job") or {}))
        self._apply_content(dict(payload.get("content") or {}))
        generation = dict(payload.get("generation") or {})
        name = str(generation.get("generation") or "")
        self.generation_label.setText(f"Generation {name}" if name else "Noch keine Generation verfügbar")
        self.backup_label.setText(f"Backups auf dem Server: {int(payload.get('backups') or 0)} von 3")
        self.last_refresh_label.setText("Live-Status aktualisiert")

    def _apply_job(self, state):
        status = str(state.get("status") or "")
        labels = {"starting": "Indexierung wird gestartet …", "running": "Indexierung läuft", "ready": "Index wird aktiviert …", "catalog": "Katalog wird aktualisiert", "content": "Dokumentinhalte werden indexiert", "publishing": "Neue Generation wird veröffentlicht", "completed": "Indexjob erfolgreich abgeschlossen", "no_changes": "Index ist aktuell", "cancelled": "Indexierung wurde abgebrochen", "error": "Indexjob ist fehlgeschlagen", "deleted": "Index gelöscht · Neuaufbau startet"}
        self.status_label.setText(labels.get(status, "Indexserver wartet"))
        active = status in {"catalog", "content", "publishing", *ACTIVE_STATUSES}
        self.progress_bar.setRange(0, 0 if active else 100)
        if not active:
            self.progress_bar.setValue(100 if status in {"completed", "no_changes"} else 0)
        details = []
        processed = int(state.get("processed_count") or state.get("indexed_count") or 0)
        if processed:
            details.append(f"{processed} Dateien geprüft")
        if state.get("current_path"):
            details.append(f"Aktuell: {state['current_path']}")
        if state.get("error"):
            details.append(f"Fehler: {state['error']}")
        self.detail_label.setText("\n".join(details))
        self.cancel_button.setVisible(active)

    def _apply_content(self, state):
        status = str(state.get("status") or "")
        done, total = int(state.get("completed_documents") or 0), int(state.get("total_documents") or 0)
        active = status in ACTIVE_STATUSES or status == "running"
        if active:
            self.cancel_button.setVisible(True)
        self.content_status_label.setText("Dokumentinhalte werden indexiert" if active else "Dateiindizierung abgeschlossen" if status == "completed" else "Dateiindizierung wartet")
        self.content_progress_bar.setRange(0, 0 if active and not total else max(1, total or 100))
        if total:
            self.content_progress_bar.setValue(min(done, total))
        pending = int(state.get("pending_documents") or 0)
        failed = int(state.get("failed_documents") or state.get("failed_count") or 0)
        details = []
        if total:
            details.append(f"{done} von {total} Dokumenten · {pending} ausstehend")
        if failed:
            details.append(f"{failed} Dokumente mit Fehlern")
        if state.get("current_path"):
            details.append(f"Aktuell: {state['current_path']}")
        if state.get("error"):
            details.append(f"Fehler: {state['error']}")
        self.content_detail_label.setText("\n".join(details) or "Noch keine Dokumentstatistik verfügbar")
        assignments = state.get("worker_assignments") or []
        assignments = assignments if isinstance(assignments, list) else []
        limit = int(state.get("worker_limit") or len(assignments))
        has_telemetry = "worker_assignments" in state
        if active and not has_telemetry:
            self.worker_summary_label.setText("Index arbeitet · Workerdetails noch nicht verfügbar")
        else:
            summary = f"{len(assignments)} von {limit or '?'} Workern aktiv" if active else "Keine aktiven Dokument-Worker"
            profile = str(state.get("resource_profile") or "")
            if active and profile:
                profile_label = {"gentle": "Schonend", "balanced": "Ausgewogen", "fast": "Schnell"}.get(profile, profile)
                summary += f" · Profil {profile_label}"
            self.worker_summary_label.setText(summary)
        worker_text = "\n".join(f"Worker {int(item.get('worker') or 0)}: {item.get('path')}" for item in assignments if isinstance(item, dict) and item.get("path"))
        if active and not has_telemetry and state.get("current_path"):
            worker_text = f"Zuletzt gemeldet: {state['current_path']}"
        self.worker_output.setPlainText(worker_text)

    def _action(self, action):
        if self.server_client:
            self._start(action)
        elif action == "cancel":
            self.cancelRequested.emit()

    def _confirm_rebuild(self):
        if QMessageBox.question(self, "Index neu aufbauen", "Kompletten Serverindex neu aufbauen?") == QMessageBox.StandardButton.Yes:
            self._action("rebuild")

    def _confirm_restart(self):
        if QMessageBox.question(
            self,
            "Container neu starten",
            "Den Docker-Indexdienst kontrolliert beenden und neu starten? "
            "Ein laufender Indexjob wird vorher abgebrochen.",
        ) == QMessageBox.StandardButton.Yes:
            self._action("restart")

    def _confirm_delete(self):
        answer = QMessageBox.warning(self, "Serverindex löschen", "Index und Generationen löschen und anschließend neu aufbauen? customers.db bleibt erhalten.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._action("delete")

    def _apply_settings(self, v):
        self.content_enabled.setChecked(bool(v.get("content_indexing_enabled", True)))
        self.resource_profile.setCurrentIndex(max(0, self.resource_profile.findData(v.get("resource_profile", "balanced"))))
        for widget, key, default in ((self.max_file_size, "max_file_size_mb", 100), (self.max_characters, "max_extracted_characters", 2000000), (self.priority_documents, "priority_documents_per_project", 24), (self.ocr_pages, "ocr_max_pages", 5), (self.ocr_extended_pages, "ocr_extended_max_pages", 25), (self.ocr_threshold, "ocr_extension_threshold", 500), (self.ocr_timeout, "ocr_timeout_seconds", 10), (self.pdf_timeout, "pdf_text_timeout_seconds", 45)):
            widget.setValue(int(v.get(key, default)))
        self.extensions.setText(str(v.get("content_extensions", "")))
        self.excluded_folders.setText(str(v.get("excluded_folders", "")))
        self.preferred_patterns.setText(str(v.get("preferred_document_patterns", "")))
        self.newest_first.setChecked(bool(v.get("newest_years_first", True)))
        self.ocr_enabled.setChecked(bool(v.get("ocr_enabled", True)))

    def _save_settings(self):
        values = {"interval_seconds": self.interval_spin.value() * 60, "content_indexing_enabled": self.content_enabled.isChecked(), "resource_profile": self.resource_profile.currentData(), "max_file_size_mb": self.max_file_size.value(), "max_extracted_characters": self.max_characters.value(), "content_extensions": self.extensions.text().strip(), "excluded_folders": self.excluded_folders.text().strip(), "preferred_document_patterns": self.preferred_patterns.text().strip(), "priority_documents_per_project": self.priority_documents.value(), "newest_years_first": self.newest_first.isChecked(), "ocr_enabled": self.ocr_enabled.isChecked(), "ocr_max_pages": self.ocr_pages.value(), "ocr_extended_max_pages": self.ocr_extended_pages.value(), "ocr_extension_threshold": self.ocr_threshold.value(), "ocr_timeout_seconds": self.ocr_timeout.value(), "pdf_text_timeout_seconds": self.pdf_timeout.value()}
        self._start("save_settings", values)

    def _refresh_log(self):
        try:
            with self.log_file.open("r", encoding="utf-8", errors="replace") as handle:
                lines = deque(handle, maxlen=5)
        except OSError:
            lines = deque()
        self.log_output.setPlainText("".join(lines).rstrip())

    def closeEvent(self, event: QCloseEvent):
        self.timer.stop()
        event.accept()
