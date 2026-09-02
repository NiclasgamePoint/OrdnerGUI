"""Independent system tray used solely to observe and control the server API."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import sys

from PySide6.QtCore import QIODevice, QThreadPool, QTimer
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from papagui_client.composition import ClientContainer
from papagui_client.presentation.tray import TrayPresenter, TrayStatusViewModel
from papagui_client.presentation.server_settings import ServerSettingsPresenter

from .main import STYLE
from .recognition_review import RecognitionReviewDialog
from .tasks import BackgroundTask


INSTANCE_NAME = "papagui-client-server-tray-v2"


class ServerTrayWindow(QMainWindow):
    def __init__(self, container: ClientContainer):
        super().__init__()
        self._container = container
        self._pool = QThreadPool(self)
        self._tasks: set[BackgroundTask] = set()
        self._presenter = TrayPresenter()
        self._settings_presenter = ServerSettingsPresenter()
        self._settings = {}
        self._settings_dirty = False
        self._settings_busy = False
        self._applying_settings = False
        self._last_activity_key = None
        self._refreshing = False
        self._shutting_down = False
        self.setWindowTitle("PapaGUI Indexserver")
        self.resize(820, 570)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_status)
        self._timer.start(5_000)
        self._animation = QTimer(self)
        self._animation.setInterval(180)
        self._animation.timeout.connect(self._animate_running_badge)
        self._build()
        QTimer.singleShot(0, self.refresh_status)

    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 18)
        header = QHBoxLayout()
        title = QLabel("Indexserver")
        title.setStyleSheet("font-size: 22px; font-weight: 700")
        header.addWidget(title)
        header.addStretch()
        self.state = QLabel("● OFFLINE")
        header.addWidget(self.state)
        outer.addLayout(header)
        self.summary = QLabel("Serverstatus wird geladen …")
        outer.addWidget(self.summary)

        tabs = QTabWidget()
        tabs.setStyleSheet(STYLE + "QTabBar { margin-left: 14px; }")
        tabs.addTab(self._overview(), "Übersicht")
        tabs.addTab(self._settings_page(), "Indexeinstellungen")
        tabs.addTab(self._activity_page(), "Aktivität")
        outer.addWidget(tabs)
        self.setCentralWidget(root)
        self._apply_view_model(self._presenter.offline("Status wird geladen"))

    def _overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.server_details = QLabel("–")
        self.server_details.setWordWrap(True)
        layout.addWidget(self.server_details)
        actions = QHBoxLayout()
        for label, action in (
            ("Index starten", "start"),
            ("Index abbrechen", "cancel"),
            ("Index löschen", "delete"),
            ("Server neu starten", "restart_server"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=action: self.run_action(value))
            actions.addWidget(button)
        layout.addLayout(actions)
        review = QPushButton("Kundenerkennung prüfen")
        review.clicked.connect(self.open_recognition_review)
        layout.addWidget(review)
        layout.addStretch()
        return page

    def open_recognition_review(self) -> None:
        control = self._container.server_control
        if control is None:
            QMessageBox.warning(
                self,
                "Kundenerkennung",
                "Diese Clientinstanz besitzt keine Admin-Steuerung.",
            )
            return
        try:
            customers = self._container.customers.list()
        except Exception:
            customers = ()
        RecognitionReviewDialog(control, customers, self).exec()

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        description = QLabel("Diese Werte gelten serverseitig ab dem nächsten Indexlauf.")
        layout.addWidget(description)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        interval = QHBoxLayout()
        self.interval_value = QSpinBox()
        self.interval_value.setRange(1, 2880)
        self.interval_unit = QComboBox()
        self.interval_unit.addItems(["Minuten", "Stunden"])
        self.interval_unit.currentTextChanged.connect(self._update_interval_range)
        self.interval_unit.currentTextChanged.connect(self._mark_settings_dirty)
        interval.addWidget(self.interval_value)
        interval.addWidget(self.interval_unit)
        form.addRow("Automatischer Lauf", interval)
        self.automatic_runs_enabled = self._check(form, "Automatische Läufe aktiv")
        self.daily_reconciliation_enabled = self._check(form, "Täglicher Abgleich")
        self.content_indexing_enabled = self._check(form, "Inhaltsindex aktiv")
        self.content_extensions = self._line(form, "Inhaltserweiterungen")
        self.excluded_folders = self._line(form, "Ausgeschlossene Ordner")
        self.max_file_size_mb = self._spin(form, "Maximale Dateigröße (MB)", 1, 102_400)
        self.max_extracted_characters = self._spin(
            form, "Maximale extrahierte Zeichen", 1, 2_000_000_000
        )
        self.ocr_enabled = self._check(form, "OCR aktiv")
        self.ocr_max_pages = self._spin(form, "OCR-Seiten", 1, 100_000)
        self.ocr_extended_max_pages = self._spin(
            form, "OCR-Seiten für bevorzugte Dokumente", 1, 100_000
        )
        self.ocr_extension_threshold = self._spin(
            form, "OCR-Erweiterungsschwelle (Zeichen)", 0, 2_000_000_000
        )
        self.ocr_timeout_seconds = self._spin(form, "OCR-Timeout (s)", 1, 86_400)
        self.pdf_text_timeout_seconds = self._spin(form, "PDF-Text-Timeout (s)", 1, 86_400)
        self.resource_profile = QComboBox()
        self.resource_profile.addItems(["gentle", "balanced", "fast"])
        form.addRow("Ressourcenprofil", self.resource_profile)
        self.resource_profile.currentTextChanged.connect(self._mark_settings_dirty)
        self.preferred_document_patterns = self._line(form, "Bevorzugte Dokumentmuster")
        self.priority_documents_per_project = self._spin(
            form, "Prioritätsdokumente je Projekt", 0, 100_000
        )
        self.newest_years_first = self._check(form, "Neueste Jahre zuerst")
        self.minimum_customer_year = self._spin(form, "Minimales Kundenjahr", 1900, 9999)
        self.interval_value.valueChanged.connect(self._mark_settings_dirty)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        row = QHBoxLayout()
        self.settings_load_button = QPushButton("Vom Server laden")
        self.settings_load_button.clicked.connect(self.load_settings)
        self.settings_save_button = QPushButton("Speichern")
        self.settings_save_button.clicked.connect(self.save_settings)
        row.addWidget(self.settings_load_button)
        row.addWidget(self.settings_save_button)
        row.addStretch()
        layout.addLayout(row)
        self.settings_status = QLabel("Noch nicht vom Server geladen")
        layout.addWidget(self.settings_status)
        return page

    def _check(self, form: QFormLayout, label: str) -> QCheckBox:
        widget = QCheckBox()
        form.addRow(label, widget)
        widget.toggled.connect(self._mark_settings_dirty)
        return widget

    def _spin(
        self, form: QFormLayout, label: str, minimum: int, maximum: int
    ) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        form.addRow(label, widget)
        widget.valueChanged.connect(self._mark_settings_dirty)
        return widget

    def _line(self, form: QFormLayout, label: str) -> QLineEdit:
        widget = QLineEdit()
        form.addRow(label, widget)
        widget.textChanged.connect(self._mark_settings_dirty)
        return widget

    def _activity_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.activity = QListWidget()
        layout.addWidget(self.activity)
        return page

    def refresh_status(self) -> None:
        if self._refreshing or self._shutting_down:
            return
        self._refreshing = True
        task = BackgroundTask(self._container.server_control.status)
        task.signals.succeeded.connect(self._show_status)
        task.signals.failed.connect(self._show_offline)
        task.signals.finished.connect(self._refresh_finished)
        self._start_task(task)

    def _show_status(self, payload) -> None:
        self._apply_view_model(self._presenter.status(payload))
        self._record_activity(payload)

    def _show_offline(self, error: str) -> None:
        self._apply_view_model(self._presenter.offline(error))

    def _apply_view_model(self, model: TrayStatusViewModel) -> None:
        self.state.setText(model.badge)
        self.state.setStyleSheet(
            f"font-weight: 700; color: {model.color}; padding: 6px 10px"
        )
        self.summary.setText(model.summary)
        self.server_details.setText(model.details)
        if model.running and not self._animation.isActive():
            self._animation.start()
        elif not model.running:
            self._animation.stop()

    def _animate_running_badge(self) -> None:
        self.state.setText(self._presenter.next_running_badge())

    def _refresh_finished(self) -> None:
        self._refreshing = False

    def run_action(self, action: str) -> None:
        if not self._ensure_admin():
            return
        self.activity.insertItem(0, f"Aktion angefordert: {action}")
        task = BackgroundTask(lambda: self._container.server_control.index_action(action))
        task.signals.succeeded.connect(self._action_complete)
        task.signals.failed.connect(self._show_action_error)
        self._start_task(task)

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
        if not self._ensure_admin():
            return
        self._set_settings_busy(True, "Servereinstellungen werden geladen …")
        task = BackgroundTask(self._container.server_control.settings)
        task.signals.succeeded.connect(lambda response: self._apply_settings(response, saved=False))
        task.signals.failed.connect(self._settings_failed)
        task.signals.finished.connect(self._settings_finished)
        self._start_task(task)

    def _apply_settings(self, response, *, saved: bool = False) -> None:
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
            ):
                getattr(self, name).setValue(int(model.values[name]))
            for name in (
                "content_extensions",
                "excluded_folders",
                "preferred_document_patterns",
            ):
                getattr(self, name).setText(str(model.values[name]))
            self.resource_profile.setCurrentText(str(model.values["resource_profile"]))
        finally:
            self._applying_settings = False
        self._settings_dirty = False
        self.settings_status.setText("Gespeichert" if saved else "Vom Server geladen")
        self.settings_save_button.setEnabled(False)

    def save_settings(self) -> None:
        if self._settings_busy:
            return
        if not self._ensure_admin():
            return
        try:
            settings = self._collect_settings()
        except ValueError as exc:
            self._settings_failed(str(exc))
            return
        self._set_settings_busy(True, "Servereinstellungen werden gespeichert …")
        task = BackgroundTask(lambda: self._container.server_control.save_settings(settings))
        task.signals.succeeded.connect(lambda response: self._apply_settings(response, saved=True))
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
            "resource_profile": self.resource_profile.currentText(),
            "preferred_document_patterns": self.preferred_document_patterns.text().strip(),
            "priority_documents_per_project": self.priority_documents_per_project.value(),
            "newest_years_first": self.newest_years_first.isChecked(),
            "minimum_customer_year": self.minimum_customer_year.value(),
        }
        return self._settings_presenter.validate(values)

    def _mark_settings_dirty(self, *_args) -> None:
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
        if unit == "Stunden":
            self.interval_value.setRange(1, 48)
        else:
            self.interval_value.setRange(15, 2880)

    def _record_activity(self, payload) -> None:
        model = self._presenter.activity(payload if isinstance(payload, Mapping) else {})
        if model.key == self._last_activity_key:
            return
        self._last_activity_key = model.key
        self.activity.insertItem(0, model.text)
        while self.activity.count() > 50:
            self.activity.takeItem(self.activity.count() - 1)

    def _ensure_admin(self) -> bool:
        if self._container.server_control.admin_unlocked:
            return True
        password, accepted = QInputDialog.getText(
            self, "Admin entsperren", "Adminpasswort", QLineEdit.EchoMode.Password
        )
        if not accepted:
            return False
        try:
            self._container.server_control.login_admin(password)
        except Exception as exc:
            self._show_action_error(str(exc))
            return False
        return True

    def _show_action_error(self, error: str) -> None:
        self.activity.insertItem(0, f"Fehler: {error}")
        QMessageBox.warning(self, "Serveraktion fehlgeschlagen", error)

    def _action_complete(self, _value) -> None:
        self.refresh_status()

    def _start_task(self, task: BackgroundTask) -> None:
        self._tasks.add(task)
        task.signals.finished.connect(self._task_finished)
        self._pool.start(task)

    def _task_finished(self) -> None:
        sender = self.sender()
        self._tasks = {task for task in self._tasks if task.signals is not sender}

    def shutdown(self) -> None:
        self._shutting_down = True
        self._timer.stop()
        self._animation.stop()
        self._pool.clear()
        for task in tuple(self._tasks):
            try:
                task.signals.disconnect()
            except RuntimeError:
                pass
        self._pool.waitForDone(1_000)


class TrayController:
    def __init__(self, application: QApplication, container: ClientContainer, show: bool):
        self.application = application
        self.window = ServerTrayWindow(container)
        self.icon = QSystemTrayIcon(
            application.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon),
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
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()

    def shutdown(self) -> None:
        self.icon.hide()
        self.window.shutdown()
        self.window.close()
        try:
            self.window._container.server_control.logout_admin()
        except Exception:
            # Shutdown must not be held hostage by an unreachable server. The
            # gateway clears its in-memory token in a finally block.
            pass


class SingleInstanceServer:
    def __init__(self):
        self.server = QLocalServer()

    def claim(self, on_show, *, request_show: bool = True) -> bool:
        if self.server.listen(INSTANCE_NAME):
            self.server.newConnection.connect(lambda: self._receive(on_show))
            return True
        socket = QLocalSocket()
        socket.connectToServer(INSTANCE_NAME, QIODevice.OpenModeFlag.WriteOnly)
        if socket.waitForConnected(300):
            if request_show:
                socket.write(b"show")
                socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return False
        QLocalServer.removeServer(INSTANCE_NAME)
        if not self.server.listen(INSTANCE_NAME):
            return True
        self.server.newConnection.connect(lambda: self._receive(on_show))
        return True

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
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("PapaGUI Indexserver")
    application.setQuitOnLastWindowClosed(False)
    application.setStyleSheet(STYLE)
    controller = TrayController(application, container, show=not options.background)
    instance = SingleInstanceServer()
    if not instance.claim(controller.show, request_show=not options.background):
        return 0
    application._papagui_tray = (controller, instance)  # type: ignore[attr-defined]
    return application.exec()
