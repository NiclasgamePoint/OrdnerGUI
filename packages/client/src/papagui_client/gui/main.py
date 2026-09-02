"""Native client shell backed only by client application services."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from papagui_client.composition import ClientContainer
from papagui_client.presentation.coordinators import ClientPage, ConflictCase
from papagui_client.application.models import GlobalSearchHit, GlobalSearchKind

from .customer_detail import CustomerDetailWidget, JournalEditorDialog
from .customer_editor import CustomerEditorDialog
from .launcher import TrayProcessLauncher
from .settings import ClientSettingsDialog, theme_stylesheet
from .tasks import BackgroundTask
from .timers import QtTimerAdapter
from .viewers import CatalogBrowserWidget


STYLE = """
QWidget { background: #edf4f7; color: #24435a; font-size: 13px; }
QMainWindow { background: #dfeaf0; }
QLineEdit, QListWidget { background: white; border: 1px solid #b8ccd7; border-radius: 8px; padding: 8px; }
QPushButton { background: #1fae9b; color: white; border: 0; border-radius: 8px; padding: 8px 14px; font-weight: 600; }
QPushButton:disabled { background: #9db7bc; }
QTabWidget::pane { border: 1px solid #b8ccd7; border-radius: 10px; background: #edf4f7; }
QTabBar::tab { padding: 9px 16px; margin: 0 2px; background: #d7e5ec; border-radius: 8px 8px 0 0; }
QTabBar::tab:selected { background: #edf4f7; color: #087d70; }
"""


class ClientMainWindow(QMainWindow):
    def __init__(self, container: ClientContainer, *, automatic_sync: bool = True):
        super().__init__()
        self._container = container
        self._pool = QThreadPool(self)
        self._tasks: set[BackgroundTask] = set()
        self._customer_views = []
        self._syncing = False
        self._closing = False
        self.setWindowTitle("PapaGUI Client")
        self.resize(1040, 720)
        self._build()
        self.refresh_customers()
        self._sync_timer = QtTimerAdapter(self)
        if automatic_sync:
            self._container.sync.start(self._sync_timer, self.synchronize, immediate=True)
        if getattr(self._container, "onboarding_required", False):
            QTimer.singleShot(0, lambda: self.open_settings(onboarding=True))

    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 20)
        header = QHBoxLayout()
        title = QLabel("PapaGUI")
        title.setStyleSheet("font-size: 25px; font-weight: 700; color: #123d56")
        header.addWidget(title)
        header.addStretch()
        self.status = QLabel("Lokale Daten bereit")
        header.addWidget(self.status)
        sync = QPushButton("Jetzt synchronisieren")
        sync.clicked.connect(self.synchronize)
        header.addWidget(sync)
        settings = QPushButton("Client-Einstellungen")
        settings.clicked.connect(self.open_settings)
        header.addWidget(settings)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._search_tab(), "Dateisuche")
        self.tabs.addTab(self._customer_tab(), "Kunden")
        self.tabs.addTab(self._connection_tab(), "Verbindung")
        self.tabs.currentChanged.connect(self._tab_changed)
        self._container.navigation.subscribe(self._navigate)
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)

    def _search_tab(self) -> QWidget:
        self.catalog_browser = CatalogBrowserWidget(self._container.search)
        self.catalog_browser.resultActivated.connect(self._search_result_activated)
        return self.catalog_browser

    def _customer_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        description = QLabel(
            "Kundendaten stammen aus dem letzten Server-Snapshot. Lokale Änderungen "
            "werden separat gespeichert und bei erreichbarem Server übertragen."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.customer_list = QListWidget()
        self.customer_list.itemDoubleClicked.connect(lambda _item: self.edit_customer())
        self.customer_list.currentRowChanged.connect(self._show_customer_detail)
        splitter.addWidget(self.customer_list)
        self.customer_detail = CustomerDetailWidget()
        self.customer_detail.journalAddRequested.connect(self.add_journal_entry)
        self.customer_detail.journalEditRequested.connect(self.edit_journal_entry)
        self.customer_detail.journalDeleteRequested.connect(self.delete_journal_entry)
        self.customer_detail.suggestionDecisionRequested.connect(
            self.decide_customer_suggestion
        )
        splitter.addWidget(self.customer_detail)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.queue_status = QLabel("Offline-Warteschlange: 0")
        layout.addWidget(self.queue_status)
        row = QHBoxLayout()
        for label, callback in (
            ("Neu", self.new_customer),
            ("Bearbeiten", self.edit_customer),
            ("Löschen", self.delete_customer),
            ("Warteschlange übertragen", self.replay_customers),
            ("Aktualisieren", self.refresh_customers),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        return page

    def _connection_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.server_label = QLabel()
        self.data_root_label = QLabel()
        layout.addWidget(self.server_label)
        layout.addWidget(self.data_root_label)
        self._refresh_connection_labels()
        layout.addStretch()
        return page

    def _refresh_connection_labels(self) -> None:
        self.server_label.setText(f"Server: {self._container.settings.server_url or '–'}")
        self.data_root_label.setText(
            f"Lokaler Datenspeicher: {self._container.settings.data_root}"
        )

    def open_settings(self, _checked: bool = False, *, onboarding: bool = False) -> None:
        dialog = ClientSettingsDialog(
            self._container.settings,
            sources=getattr(self._container, "config_sources", {}),
            onboarding=onboarding,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            effective = self._container.save_client_settings(dialog.settings())
        except Exception as exc:
            QMessageBox.warning(self, "Einstellungen nicht gespeichert", str(exc))
            return
        application = QApplication.instance()
        if application is not None:
            application.setStyleSheet(theme_stylesheet(effective.theme, STYLE))
        self._refresh_connection_labels()
        self.status.setText("Client-Einstellungen gespeichert")

    def synchronize(self) -> None:
        if self._syncing or self._closing:
            return
        self._syncing = True
        self.status.setText("Synchronisierung läuft …")
        task = BackgroundTask(self._container.sync.sync)
        task.signals.succeeded.connect(self._sync_complete)
        task.signals.failed.connect(self._sync_failed)
        task.signals.finished.connect(self._sync_finished)
        self._start_task(task)

    def _sync_complete(self, result) -> None:
        self.status.setText(
            "Synchronisiert · " + ", ".join(result.changed_components)
            if result.changed
            else "Synchronisiert · aktuell"
        )
        if result.customer_replay is not None:
            self._handle_conflicts(result.customer_replay.conflicts)
        if result.journal_replay is not None:
            self._handle_journal_replay(result.journal_replay)
        self.refresh_customers()
        if self.catalog_browser.query_input.text().strip():
            self.catalog_browser.run_search()

    def _sync_finished(self) -> None:
        self._syncing = False

    def _sync_failed(self, error: str) -> None:
        self.status.setText(f"Offline · {error}")

    def refresh_customers(self) -> None:
        try:
            self._customer_views = list(self._container.customers.list())
        except Exception as exc:
            self.customer_list.clear()
            self.customer_list.addItem(f"Kundendaten nicht verfügbar: {exc}")
            return
        self.customer_list.clear()
        self.customer_detail.set_customer(None)
        pending = 0
        badges = {
            "pending": "⏳ lokal vorgemerkt",
            "conflict": "⚠ Konflikt",
            "awaiting_snapshot": "✓ übertragen",
            "synced": "",
        }
        for view in self._customer_views:
            customer = view.customer
            location = f" · {customer.city}" if customer.city else ""
            badge = badges[view.sync_state.value]
            if view.sync_state.value in {"pending", "conflict"}:
                pending += 1
            suffix = f"  [{badge}]" if badge else ""
            item = QListWidgetItem(f"{customer.display_name}{location}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, view.key)
            self.customer_list.addItem(item)
        self.queue_status.setText(f"Offline-Warteschlange: {pending}")
        if self._customer_views:
            self.customer_list.setCurrentRow(0)

    def new_customer(self) -> None:
        dialog = CustomerEditorDialog(parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._save_customer(dialog.customer())

    def edit_customer(self) -> None:
        selected = self._selected_customer()
        if selected is None:
            return
        dialog = CustomerEditorDialog(selected.customer, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._save_customer(dialog.customer(), local_key=selected.key if selected.customer.id is None else None)

    def delete_customer(self) -> None:
        selected = self._selected_customer()
        if selected is None:
            return
        answer = QMessageBox.question(
            self,
            "Kunde löschen",
            f"Soll „{selected.customer.display_name}“ wirklich gelöscht werden?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self._container.customers.delete(
                selected.customer,
                local_key=selected.key if selected.customer.id is None else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Löschen fehlgeschlagen", str(exc))
            return
        self._handle_write_result(result)

    def replay_customers(self) -> None:
        try:
            result = self._container.customers.replay()
        except Exception as exc:
            QMessageBox.warning(self, "Übertragung fehlgeschlagen", str(exc))
            return
        self._handle_conflicts(result.conflicts)
        self.refresh_customers()

    def _show_customer_detail(self, row: int) -> None:
        selected = self._customer_views[row] if 0 <= row < len(self._customer_views) else None
        customer = selected.customer if selected is not None else None
        self.customer_detail.set_customer(customer)
        if customer is None or customer.id is None:
            self.customer_detail.set_journal(())
            self.customer_detail.set_suggestions((), 0)
            return
        try:
            journals = getattr(self._container, "journals", None)
            self.customer_detail.set_journal(
                journals.list(customer.id) if journals is not None else ()
            )
        except Exception:
            self.customer_detail.set_journal(())
        try:
            suggestions, revision = self._container.review_gateway.customer_suggestions(
                customer.id, "pending"
            )
            self.customer_detail.set_suggestions(suggestions, revision)
        except Exception:
            self.customer_detail.set_suggestions((), customer.revision)

    def add_journal_entry(self) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None:
            return
        dialog = JournalEditorDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            result = self._container.journals.save(
                selected.customer.id,
                dialog.entry(),
                selected.customer.revision,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Journal konnte nicht gespeichert werden", str(exc))
        else:
            self._handle_journal_replay(result.replay)
        self._show_customer_detail(self.customer_list.currentRow())

    def edit_journal_entry(self, view) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None:
            return
        dialog = JournalEditorDialog(view.entry, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            result = self._container.journals.save(
                selected.customer.id,
                dialog.entry(),
                selected.customer.revision,
                local_key=view.key if view.entry.id is None else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Journal konnte nicht gespeichert werden", str(exc))
        else:
            self._handle_journal_replay(result.replay)
        self._show_customer_detail(self.customer_list.currentRow())

    def delete_journal_entry(self, view) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None:
            return
        try:
            if view.entry.id is None:
                self._container.journals.discard(view.key)
            else:
                result = self._container.journals.delete(
                    selected.customer.id,
                    view.entry.id,
                    selected.customer.revision,
                )
                self._handle_journal_replay(result.replay)
        except Exception as exc:
            QMessageBox.warning(self, "Journaleintrag konnte nicht gelöscht werden", str(exc))
        self._show_customer_detail(self.customer_list.currentRow())

    def _handle_journal_replay(self, replay) -> None:
        if not replay.conflicts:
            return
        for case in self._container.journals.conflicts():
            self._resolve_journal_conflict(case)

    def _resolve_journal_conflict(self, case) -> None:
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Gleichzeitige Journaländerung")
        message.setText(
            "Der Kunde wurde auf einem anderen Client geändert. "
            "Die lokale Journaländerung wurde nicht überschrieben."
        )
        reload_button = message.addButton("Neu laden", QMessageBox.ButtonRole.AcceptRole)
        merge_button = message.addButton(
            "Manuell zusammenführen", QMessageBox.ButtonRole.ActionRole
        )
        operation = getattr(getattr(case.mutation, "operation", None), "value", None)
        retry_label = "Erneut löschen" if operation == "delete" else "Erneut speichern"
        retry_button = message.addButton(retry_label, QMessageBox.ButtonRole.ActionRole)
        discard_button = message.addButton(
            "Lokale Änderung verwerfen", QMessageBox.ButtonRole.DestructiveRole
        )
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.exec()
        clicked = message.clickedButton()
        try:
            if clicked in {reload_button, discard_button}:
                self._container.journals.discard_conflict(case)
            elif clicked is retry_button:
                self._container.journals.retry_against_current(case)
            elif clicked is merge_button and case.local is not None:
                dialog = JournalEditorDialog(case.local, parent=self)
                if dialog.exec() == dialog.DialogCode.Accepted:
                    self._container.journals.retry_against_current(case, dialog.entry())
        except Exception as exc:
            QMessageBox.warning(self, "Journalkonflikt konnte nicht gelöst werden", str(exc))

    def _search_result_activated(self, hit) -> None:
        if not isinstance(hit, GlobalSearchHit):
            return
        if hit.record.kind is not GlobalSearchKind.CUSTOMER or hit.record.customer_id is None:
            return
        key = str(hit.record.customer_id)
        for row, view in enumerate(self._customer_views):
            if view.key == key:
                self.tabs.setCurrentIndex(1)
                self.customer_list.setCurrentRow(row)
                break

    def decide_customer_suggestion(self, suggestion, action: str, revision: int) -> None:
        try:
            self._container.review_gateway.decide_suggestion(
                suggestion.customer_id,
                suggestion.id,
                action,
                revision,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Vorschlag konnte nicht verarbeitet werden", str(exc))
            return
        self._show_customer_detail(self.customer_list.currentRow())

    def _save_customer(self, customer, *, local_key=None) -> None:
        try:
            result = self._container.customers.save(customer, local_key=local_key)
        except Exception as exc:
            QMessageBox.warning(self, "Speichern fehlgeschlagen", str(exc))
            return
        self._handle_write_result(result)

    def _handle_write_result(self, result) -> None:
        self._handle_conflicts(result.replay.conflicts)
        self.refresh_customers()

    def _handle_conflicts(self, _conflicts) -> None:
        for case in self._container.customers.conflicts():
            self._resolve_conflict(case)

    def _resolve_conflict(self, case: ConflictCase) -> None:
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Gleichzeitige Kundenänderung")
        message.setText("Dieser Kunde wurde zwischenzeitlich auf einem anderen Client geändert.")
        message.setInformativeText(
            "Wähle bewusst, wie die lokale Änderung behandelt werden soll. Es wird nichts still überschrieben."
        )
        reload_button = message.addButton("Neu laden", QMessageBox.ButtonRole.AcceptRole)
        merge_button = message.addButton("Manuell zusammenführen", QMessageBox.ButtonRole.ActionRole)
        operation = getattr(getattr(case.mutation, "operation", None), "value", None)
        retry_label = "Erneut löschen" if operation == "delete" else "Erneut speichern"
        retry_button = message.addButton(retry_label, QMessageBox.ButtonRole.ActionRole)
        discard_button = message.addButton("Lokale Änderung verwerfen", QMessageBox.ButtonRole.DestructiveRole)
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.exec()
        clicked = message.clickedButton()
        try:
            if clicked is reload_button:
                self._container.customers.reload_from_server(case)
            elif clicked is discard_button:
                self._container.customers.discard_local(case)
            elif clicked is retry_button:
                self._container.customers.retry_against_current(case)
            elif clicked is merge_button and case.local is not None:
                server_name = case.current.display_name if case.current is not None else "nicht verfügbar"
                dialog = CustomerEditorDialog(
                    case.local,
                    explanation=f"Aktueller Serverwert: {server_name}. Passe die zusammengeführte Fassung an.",
                    parent=self,
                )
                if dialog.exec() == dialog.DialogCode.Accepted:
                    self._container.customers.save_merge(case, dialog.customer())
        except Exception as exc:
            QMessageBox.warning(self, "Konfliktlösung fehlgeschlagen", str(exc))

    def _selected_customer(self):
        row = self.customer_list.currentRow()
        return self._customer_views[row] if 0 <= row < len(self._customer_views) else None

    def _tab_changed(self, index: int) -> None:
        pages = (ClientPage.SEARCH, ClientPage.CUSTOMERS, ClientPage.CONNECTION)
        if 0 <= index < len(pages):
            self._container.navigation.navigate(pages[index])

    def _navigate(self, page: ClientPage) -> None:
        pages = (ClientPage.SEARCH, ClientPage.CUSTOMERS, ClientPage.CONNECTION)
        self.tabs.setCurrentIndex(pages.index(page))

    def _start_task(self, task: BackgroundTask) -> None:
        self._tasks.add(task)
        task.signals.finished.connect(self._task_finished)
        self._pool.start(task)

    def _task_finished(self) -> None:
        sender = self.sender()
        self._tasks = {task for task in self._tasks if task.signals is not sender}

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self._container.sync.stop()
        self.catalog_browser.shutdown()
        self._pool.clear()
        for task in tuple(self._tasks):
            try:
                task.signals.disconnect()
            except RuntimeError:
                pass
        self._pool.waitForDone(1_000)
        super().closeEvent(event)


def forced_fullscreen() -> bool:
    return os.getenv("PAPAGUI_FORCE_FULLSCREEN", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def show_main_window(application: QApplication, window: ClientMainWindow) -> None:
    if not forced_fullscreen():
        window.show()
        return
    screen = window.screen() or application.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        window.setGeometry(geometry)
    window.show()
    if screen is not None:
        geometry = screen.availableGeometry()
        window.move(geometry.topLeft())
        window.resize(geometry.size())
    window.showFullScreen()


def run_main_gui(container: ClientContainer, *, automatic_sync: bool = True) -> int:
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("PapaGUI Client")
    application.setStyleSheet(theme_stylesheet(container.settings.theme, STYLE))
    try:
        TrayProcessLauncher().start()
    except OSError:
        # The main client remains usable if the platform temporarily rejects a
        # detached process; users can still launch papagui-tray independently.
        pass
    window = ClientMainWindow(container, automatic_sync=automatic_sync)
    show_main_window(application, window)
    application._papagui_window = window  # type: ignore[attr-defined]
    return application.exec()
