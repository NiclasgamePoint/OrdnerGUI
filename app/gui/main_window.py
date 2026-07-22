from __future__ import annotations

import logging
import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMenu,
    QMainWindow,
    QMessageBox,
    QStyle,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.core.config import (
    CUSTOMER_DB_FILE,
    DB_FILE,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
    get_configured_index_source,
    has_configured_index_source,
    load_customer_recognition_options,
    load_index_options,
    save_customer_recognition_options,
    save_index_options,
    save_index_source,
)
from app.core.customer_repository import CustomerRepository
from app.core.index_diagnostics import IndexDiagnosticsService
from app.core.index_manager import IndexManager
from app.core.index_store import (
    activate_index,
    create_restore_build,
    validate_index,
)
from app.core.search_models import RecentCustomerHistory, SearchFilters, SearchHistory
from app.gui.dialogs import (
    CustomerEditorDialog,
    CustomerRecognitionReviewDialog,
    OnboardingDialog,
)
from app.gui.navigation import NavigationController, NavigationEntry
from app.gui.pages import CustomerPage, FolderPage, SearchPage
from app.gui.settings_popup import SettingsPopup
from app.gui.theme import ThemeManager
from app.gui.widgets import AppHeader, IndexStatusBar, SearchFilterPopup
from app.gui.workers import IndexJobController, SearchWorker, SettingsDataWorker
from app.services import FileSystemMonitor
from app.services.document_converter import DocumentConverter


LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Application shell coordinating pages, search, settings and background jobs."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setGeometry(100, 100, WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(920, 640)

        self.index_options = load_index_options()
        self.recognition_options = load_customer_recognition_options()
        self.index_controller = IndexJobController(DB_FILE, parent=self)
        self.index_controller.adopt_running_job()
        self.index_manager = IndexManager(DB_FILE, options=self.index_options)
        self.customer_repository = CustomerRepository(CUSTOMER_DB_FILE)
        self.index_source = get_configured_index_source()
        self.pending_index_source: Path | None = None
        self.theme_manager = ThemeManager()
        self.diagnostics_service = IndexDiagnosticsService()
        self.settings_popup: SettingsPopup | None = None
        self.settings_data_worker: SettingsDataWorker | None = None
        self.filesystem_monitor: FileSystemMonitor | None = None
        self.pending_filesystem_sync = False
        self.tray_icon: QSystemTrayIcon | None = None
        self.source_reconnect_timer = QTimer(self)
        self.source_reconnect_timer.setInterval(10_000)
        self.source_reconnect_timer.timeout.connect(self._try_reconnect_source)

        self.search_generation = 0
        self.search_workers: set[SearchWorker] = set()
        self.search_counts: dict[str, int | None] = {
            "customers": None,
            "folders": None,
        }
        self.search_history = SearchHistory()
        self.recent_customer_history = RecentCustomerHistory(maximum=5)
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(250)
        self.search_debounce.timeout.connect(self._start_live_search)
        self.initialization_timer = QTimer(self)
        self.initialization_timer.setSingleShot(True)
        self.initialization_timer.timeout.connect(self._initialize_data_source)

        self.navigator = NavigationController(parent=self)
        self._reconcile_source_from_completed_job()
        self._build_ui()
        self._init_system_tray()
        self._connect_signals()
        self._refresh_search_facets()
        self.apply_theme()
        self.navigator.reset("search")
        self._show_initial_customers()
        self.initialization_timer.start(0)

    def _initialize_data_source(self):
        if not has_configured_index_source():
            dialog = OnboardingDialog(parent=self)
            if dialog.exec() != QDialog.Accepted or dialog.selected_path is None:
                self.status_bar.set_text(
                    "Noch keine Datenquelle eingerichtet · über Einstellungen fortfahren"
                )
                return
            self.index_source = dialog.selected_path
            save_index_source(self.index_source)
        self.check_and_index()
        self._start_filesystem_monitor()

    def _try_reconnect_source(self):
        if not self.index_source.exists() or not self.index_source.is_dir():
            return
        self.source_reconnect_timer.stop()
        self.status_bar.set_text(
            f"Datenquelle wieder erreichbar ✓ · prüfe {self.index_source.name} …"
        )
        self.check_and_index()
        self._start_filesystem_monitor()

    def _reconcile_source_from_completed_job(self):
        previous_job = self.index_controller.current_state()
        indexed_root = self.index_manager.get_metadata("index_root")
        if (
            previous_job.get("status") == "completed"
            and previous_job.get("activated_by") == "worker"
            and indexed_root
            and indexed_root == previous_job.get("source")
        ):
            self.index_source = Path(indexed_root)
            save_index_source(self.index_source)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("RootWidget")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        self.header = AppHeader(self.search_history.entries())
        layout.addWidget(self.header)

        self.filter_popup = SearchFilterPopup(self)
        self.filter_popup.filtersChanged.connect(self._on_filter_changed)
        self.domain_filter = self.filter_popup.domain_combo
        self.year_filter = self.filter_popup.year_combo
        self.file_type_filter = self.filter_popup.file_type_combo

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("PageStack")
        self.search_page = SearchPage()
        self.customer_page = CustomerPage(self.customer_repository)
        self.folder_page = FolderPage()
        for page in (self.search_page, self.customer_page, self.folder_page):
            self.page_stack.addWidget(page)
        layout.addWidget(self.page_stack, 1)

        self.status_bar = IndexStatusBar()
        layout.addWidget(self.status_bar)
        self.setCentralWidget(root)

        back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        back_shortcut.activated.connect(self.navigator.back)
        forward_shortcut = QShortcut(QKeySequence("Alt+Right"), self)
        forward_shortcut.activated.connect(self.navigator.forward)

    def _connect_signals(self):
        self.header.queryChanged.connect(self.on_search_text_changed)
        self.header.searchRequested.connect(self.start_full_search)
        self.header.filterRequested.connect(self.open_filter_popup)
        self.header.settingsRequested.connect(self.open_settings_popup)

        self.search_page.customerActivated.connect(self.open_customer_page)
        self.search_page.folderActivated.connect(self.open_folder_page)
        self.search_page.openPathRequested.connect(self.open_native_path)

        self.customer_page.backRequested.connect(self.navigator.back)
        self.customer_page.folderActivated.connect(self.open_folder_page)
        self.customer_page.openPathRequested.connect(self.open_native_path)
        self.customer_page.customerChanged.connect(self._on_customer_changed)

        self.folder_page.backRequested.connect(self.navigator.back)
        self.folder_page.openPathRequested.connect(self.open_native_path)
        self.folder_page.manageCustomerRequested.connect(self.manage_folder_customer)

        self.status_bar.cancelRequested.connect(self.cancel_background_indexing)
        self.status_bar.detailsRequested.connect(self.open_index_diagnostics)
        self.status_bar.textChanged.connect(self._update_system_tray)
        self.status_bar.busyChanged.connect(lambda _busy: self._update_system_tray())
        self.navigator.routeChanged.connect(self._show_route)

        self.index_controller.progress.connect(self.on_indexing_progress)
        self.index_controller.ready.connect(self.on_index_ready)
        self.index_controller.finished.connect(self.on_indexing_complete)

    def _show_route(self, entry: NavigationEntry):
        if entry.page == "search":
            self.page_stack.setCurrentWidget(self.search_page)
            return
        if entry.page == "customer":
            customer = self.customer_repository.get(int(entry.payload))
            if customer is None:
                self.status_bar.set_text("Der ausgewählte Kunde existiert nicht mehr.")
                QTimer.singleShot(0, self.navigator.back)
                return
            folder_paths = customer.folder_paths or (
                [customer.folder_path] if customer.folder_path else []
            )
            summaries = {
                path: self.index_manager.get_folder_summary(path)
                for path in folder_paths
            }
            self.customer_page.set_customer(customer, summaries)
            self.page_stack.setCurrentWidget(self.customer_page)
            return
        if entry.page == "folder":
            try:
                details = self.index_manager.get_folder_details(str(entry.payload))
            except Exception as exc:
                QMessageBox.warning(self, "Ordner konnte nicht geladen werden", str(exc))
                QTimer.singleShot(0, self.navigator.back)
                return
            self.folder_page.set_folder(details)
            self.page_stack.setCurrentWidget(self.folder_page)
            self.status_bar.set_text(
                f"Ordner: {details['folder_name']} · {details['file_count']} Dateien"
            )

    def _init_system_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray_menu = QMenu(self)
        open_action = tray_menu.addAction("PapaGUI öffnen")
        open_action.triggered.connect(self._show_from_tray)
        close_action = tray_menu.addAction("Beenden")
        close_action.triggered.connect(self.close)

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self._update_system_tray()
        self.tray_icon.show()

    def _update_system_tray(self):
        if self.tray_icon is None:
            return
        status_text = self.status_bar.status_label.text().strip() or "Bereit"
        self.tray_icon.setToolTip(f"PapaGUI\n{status_text}")

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self._show_from_tray()

    def open_customer_page(self, customer_id: int):
        self.navigator.navigate("customer", customer_id)

    def open_folder_page(self, folder_path: str):
        if folder_path:
            self.navigator.navigate("folder", folder_path)

    def open_native_path(self, path_value: str):
        path = Path(path_value).expanduser()
        if not path.exists():
            QMessageBox.warning(
                self,
                "Ordner nicht gefunden",
                f"Der Ordner wurde nicht gefunden:\n{path}",
            )
            return
        target = path if path.is_dir() else path.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve()))):
            QMessageBox.warning(
                self,
                "Ordner konnte nicht geöffnet werden",
                f"Kein Standardprogramm für den Pfad gefunden oder Start fehlgeschlagen:\n{target}",
            )

    def manage_folder_customer(self, folder_path: str, folder_name: str):
        dialog = CustomerEditorDialog(
            repository=self.customer_repository,
            folder_path=folder_path,
            suggested_name=folder_name,
            parent=self,
        )
        if not dialog.exec():
            return
        customer = self.customer_repository.get_by_folder(folder_path)
        self._refresh_customer_results_only()
        if customer is not None and customer.id is not None:
            self.navigator.navigate("customer", customer.id)

    def _on_customer_changed(self, _customer_id: int):
        self._refresh_customer_results_only()
        if self.navigator.current.page == "customer":
            self._show_route(self.navigator.current)

    def _show_initial_customers(self):
        customers = [
            customer
            for customer_id in self.recent_customer_history.ids()
            if (customer := self.customer_repository.get(customer_id)) is not None
        ]
        self.search_page.reset(customers)
        self.search_counts = {"customers": len(customers), "folders": None}
        if customers:
            self.status_bar.set_text(
                f"{len(customers)} zuletzt gesuchte Kunden · Suchbegriff eingeben"
            )
        else:
            self.status_bar.set_text("Suchbegriff für Kunden oder Ordner eingeben")

    def _refresh_customer_results_only(self):
        query = self.header.query()
        if query:
            customers = self.customer_repository.search(
                query,
                self.index_options.result_limit,
            )
        else:
            self._show_initial_customers()
            return
        self.search_page.set_customers(customers, len(customers))

    def on_search_text_changed(self, text: str):
        self.search_generation += 1
        self._cancel_outdated_searches()
        self.search_debounce.stop()
        self.navigator.navigate("search")
        query = text.strip()
        self.search_counts = {"customers": None, "folders": None}
        if not query:
            self._show_initial_customers()
            return
        if len(query) < 2:
            self.search_page.show_short_query_hint()
            self.status_bar.set_text("Bitte mindestens zwei Zeichen eingeben.")
            return
        self.search_page.prepare_search()
        self.status_bar.set_text("Kunden- und Ordnersuche wird vorbereitet …")
        self.search_debounce.start()

    def _start_live_search(self):
        query = self.header.query()
        if len(query) >= 2:
            self._launch_visible_searches(query, self.search_generation)

    def start_full_search(self):
        query = self.header.query()
        if len(query) < 2:
            self.status_bar.set_text("Bitte mindestens zwei Zeichen eingeben.")
            return
        self.search_debounce.stop()
        self.search_generation += 1
        self._cancel_outdated_searches()
        self.navigator.navigate("search")
        self.header.set_history(self.search_history.add(query))
        self.search_counts = {"customers": None, "folders": None}
        self.search_page.prepare_search()
        self.status_bar.set_text("Durchsuche Kunden und Ordner parallel …")
        self._launch_visible_searches(query, self.search_generation)

    def _launch_visible_searches(self, query: str, generation: int):
        for category in ("customers", "folders"):
            worker = SearchWorker(
                DB_FILE,
                generation,
                category,
                query,
                self.index_options.result_limit,
                self._current_search_filters(),
                1,
                self.index_options.result_limit,
                CUSTOMER_DB_FILE,
            )
            worker.completed.connect(self._on_search_completed)
            worker.finished.connect(
                lambda worker=worker: self._release_search_worker(worker)
            )
            self.search_workers.add(worker)
            worker.start()

    def _on_search_completed(
        self,
        generation: int,
        category: str,
        results,
        error: str,
    ):
        if generation != self.search_generation:
            return
        if error:
            if category == "customers":
                self.search_page.set_customer_error(error)
            elif category == "folders":
                self.search_page.set_folder_error(error)
            self.search_counts[category] = 0
        else:
            page = results
            self.search_counts[category] = page.total
            if category == "customers":
                self.search_page.set_customers(page.items, page.total)
                self.recent_customer_history.remember([
                    int(customer.id)
                    for customer in page.items
                    if customer.id is not None
                ])
            elif category == "folders":
                self.search_page.set_folders(page.items, page.total)
        self._update_search_status()

    def _update_search_status(self):
        customers = self.search_counts["customers"]
        folders = self.search_counts["folders"]
        customer_text = "…" if customers is None else str(customers)
        folder_text = "…" if folders is None else str(folders)
        self.status_bar.set_text(
            f"Kunden: {customer_text} · Ordner: {folder_text}"
        )

    def _cancel_outdated_searches(self):
        for worker in tuple(self.search_workers):
            worker.requestInterruption()

    def _release_search_worker(self, worker: SearchWorker):
        self.search_workers.discard(worker)
        worker.deleteLater()

    def _current_search_filters(self) -> SearchFilters:
        return SearchFilters(
            domain_folder=str(self.domain_filter.currentData() or ""),
            year=str(self.year_filter.currentData() or ""),
            file_type=str(self.file_type_filter.currentData() or ""),
        )

    def _refresh_search_facets(self):
        try:
            facets = self.index_manager.get_search_facets()
        except Exception:
            return
        configurations = (
            (self.domain_filter, "Alle Themen", facets.get("domains", [])),
            (self.year_filter, "Alle Jahre/Vorlagen", facets.get("years", [])),
            (self.file_type_filter, "Alle Dateitypen", facets.get("file_types", [])),
        )
        for combo, empty_label, values in configurations:
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(empty_label, "")
            for value in values:
                combo.addItem(str(value), str(value))
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self._update_filter_button()

    def _on_filter_changed(self):
        self._update_filter_button()
        if len(self.header.query()) >= 2:
            self.start_full_search()

    def _update_filter_button(self):
        self.header.set_filter_count(self.filter_popup.active_filter_count())

    def open_filter_popup(self):
        if self.filter_popup.isVisible():
            self.filter_popup.close()
            return
        self.filter_popup.adjustSize()
        button = self.header.filter_button
        anchor = button.mapToGlobal(button.rect().bottomLeft())
        available = self.screen().availableGeometry()
        popup_x = min(anchor.x(), available.right() - self.filter_popup.width())
        popup_y = anchor.y() + 6
        if popup_y + self.filter_popup.height() > available.bottom():
            button_top = button.mapToGlobal(button.rect().topLeft()).y()
            popup_y = button_top - self.filter_popup.height() - 6
        self.filter_popup.move(
            max(available.left(), popup_x),
            max(available.top(), popup_y),
        )
        self.filter_popup.show()
        self.filter_popup.raise_()

    def apply_theme(self):
        app = QApplication.instance()
        if app is not None:
            self.theme_manager.apply(app)

    def open_settings_popup(self):
        self.filter_popup.close()
        if self.settings_popup is not None and self.settings_popup.isVisible():
            self.settings_popup.close()
            return
        self.settings_popup = SettingsPopup(
            self.theme_manager.mode,
            self.theme_manager.accent,
            self,
            contrast=self.theme_manager.contrast,
            font_size=self.theme_manager.font_size,
            data_path=self.index_source,
            indexing=self.index_controller.is_active(),
            backups=[],
            index_options=self.index_options,
            diagnostics=None,
            recognition_options=self.recognition_options,
            recognition_summary={},
            pending_recognition_cases=0,
        )
        self.settings_popup.set_backups_loading()
        self.settings_popup.set_diagnostics_loading()
        self.settings_popup.set_recognition_state_loading()
        self.settings_popup.appearanceChanged.connect(
            self.on_settings_appearance_changed
        )
        self.settings_popup.dataPathChanged.connect(
            self.on_settings_data_path_changed
        )
        self.settings_popup.reindexRequested.connect(
            self.on_settings_reindex_requested
        )
        self.settings_popup.cancelIndexRequested.connect(
            self.cancel_background_indexing
        )
        self.settings_popup.loadBackupRequested.connect(
            self.on_load_backup_requested
        )
        self.settings_popup.indexOptionsChanged.connect(
            self.on_index_options_changed
        )
        self.settings_popup.customerRecognitionOptionsChanged.connect(
            self.on_customer_recognition_options_changed
        )
        self.settings_popup.reviewRecognitionRequested.connect(
            self.open_customer_recognition_review
        )
        self.settings_popup.clearCustomerDataRequested.connect(
            self.confirm_clear_customer_data
        )
        self.settings_popup.destroyed.connect(self._clear_settings_popup)
        self.settings_popup.resize(self.settings_popup.size_for_parent())
        self._center_settings_popup()
        self.settings_popup.show()
        self.settings_popup.raise_()
        self._refresh_settings_popup_data()

    def open_index_diagnostics(self):
        if self.settings_popup is None or not self.settings_popup.isVisible():
            self.open_settings_popup()
        if self.settings_popup is not None:
            self.settings_popup.nav_list.setCurrentRow(1)

    def _start_settings_data_load(self):
        if self.settings_data_worker is not None and self.settings_data_worker.isRunning():
            self.settings_data_worker.requestInterruption()
            self.settings_data_worker.wait()
        self.settings_data_worker = SettingsDataWorker(
            DB_FILE,
            CUSTOMER_DB_FILE,
            parent=self,
        )
        self.settings_data_worker.completed.connect(self._on_settings_data_loaded)
        self.settings_data_worker.finished.connect(self._release_settings_data_worker)
        self.settings_data_worker.start()

    def _refresh_settings_popup_data(self):
        if self.settings_popup is None:
            return
        self.settings_popup.set_backups_loading()
        self.settings_popup.set_diagnostics_loading()
        self.settings_popup.set_recognition_state_loading()
        self._start_settings_data_load()

    def _on_settings_data_loaded(self, payload):
        if self.settings_popup is None:
            return
        self.settings_popup.set_backups(payload.get("backups", []))
        self.settings_popup.set_diagnostics(payload.get("diagnostics"))
        self.settings_popup.set_recognition_state(
            payload.get("recognition_summary") or {},
            int(payload.get("pending_recognition_cases") or 0),
        )
        error = str(payload.get("error") or "")
        if error:
            self.status_bar.set_text(f"Einstellungsdaten konnten nicht geladen werden: {error}")

    def _release_settings_data_worker(self):
        worker = self.sender()
        if isinstance(worker, SettingsDataWorker):
            worker.deleteLater()
            if self.settings_data_worker is worker:
                self.settings_data_worker = None

    def confirm_clear_customer_data(self):
        if self.index_controller.is_active():
            QMessageBox.information(
                self,
                "Kundendaten löschen",
                "Bitte die laufende Indexierung zuerst abschließen.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Kundendaten löschen",
            "Alle Kunden, Kontakte, Projekte, Dienstleistungstypen, Prüffälle "
            "und Vorschläge werden gelöscht.\n\n"
            "Der Dokumentindex bleibt erhalten. Neue Kunden entstehen erst beim "
            "nächsten Index-/Erkennungslauf.\n\n"
            "Kundendaten wirklich löschen?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.clear_customer_data()

    def clear_customer_data(self):
        try:
            self.customer_repository.clear_all_customer_data()
            self.recent_customer_history.clear()
            self.customer_repository.close()
            self.customer_repository = CustomerRepository(CUSTOMER_DB_FILE)
            self.navigator.reset("search")
            self._show_initial_customers()
            self.status_bar.set_text("Kundendaten gelöscht · Kundenliste ist leer")
            if self.settings_popup is not None:
                self.settings_popup.set_recognition_state({}, 0)
        except Exception as exc:
            self.customer_repository = CustomerRepository(CUSTOMER_DB_FILE)
            QMessageBox.warning(
                self,
                "Kundendaten konnten nicht gelöscht werden",
                str(exc),
            )

    def on_customer_recognition_options_changed(self, options):
        self.recognition_options = options
        save_customer_recognition_options(options)
        self.status_bar.set_text(
            "Kundenerkennung gespeichert · wird beim nächsten Indexlauf angewendet"
        )

    def open_customer_recognition_review(self):
        if self.settings_popup is not None:
            self.settings_popup.close()
        dialog = CustomerRecognitionReviewDialog(
            DB_FILE,
            CUSTOMER_DB_FILE,
            self.recognition_options,
            parent=self,
        )
        dialog.customersChanged.connect(self._refresh_customer_results_only)
        dialog.casesChanged.connect(
            lambda count: self.status_bar.set_text(
                f"Kundenerkennung · {count} offene Prüffälle"
                if count else "Kundenerkennung vollständig geprüft ✓"
            )
        )
        dialog.exec()
        self._refresh_customer_results_only()

    def _center_settings_popup(self):
        if self.settings_popup is None:
            return
        center = self.mapToGlobal(self.rect().center())
        self.settings_popup.move(
            center.x() - self.settings_popup.width() // 2,
            center.y() - self.settings_popup.height() // 2,
        )

    def _clear_settings_popup(self):
        self.settings_popup = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.settings_popup is not None and self.settings_popup.isVisible():
            self.settings_popup.resize(self.settings_popup.size_for_parent())
            QTimer.singleShot(0, self._center_settings_popup)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.settings_popup is not None and self.settings_popup.isVisible():
            QTimer.singleShot(0, self._center_settings_popup)

    def on_settings_appearance_changed(
        self, mode: str, accent: str, contrast: int, font_size: int
    ):
        self.theme_manager.set_mode(mode)
        self.theme_manager.set_accent(accent)
        self.theme_manager.set_contrast(contrast)
        self.theme_manager.set_font_size(font_size)
        self.theme_manager.save()
        self.apply_theme()

    def on_settings_data_path_changed(self, path_value: str):
        new_source = Path(path_value).expanduser().resolve()
        if not new_source.exists() or not new_source.is_dir():
            QMessageBox.warning(
                self,
                "Datenquelle",
                "Der ausgewählte Datenordner ist ungültig.",
            )
            return
        self.source_reconnect_timer.stop()
        if self.index_controller.is_active():
            QMessageBox.information(
                self,
                "Indexierung läuft",
                "Bitte warten, bis die aktuelle Indexierung abgeschlossen ist.",
            )
            return
        if (
            new_source == self.index_source
            and self.index_manager.has_index_for_root(new_source)
        ):
            save_index_source(new_source)
            self.status_bar.set_text(f"Datenquelle aktiv ✓ ({new_source.name})")
            return
        self.pending_index_source = new_source
        self._start_background_indexing(
            new_source,
            full_rebuild=True,
            status_text=f"Baue Index für {new_source} neu auf …",
        )

    def on_settings_reindex_requested(self):
        if self.index_controller.is_active():
            if self.settings_popup is not None:
                self.settings_popup.set_indexing(True)
            return
        if not self.index_source.exists() or not self.index_source.is_dir():
            if self.settings_popup is not None:
                self.settings_popup.set_indexing(False)
            QMessageBox.warning(
                self,
                "Indexierung",
                "Die konfigurierte Datenquelle existiert nicht.",
            )
            return
        full_rebuild = not self.index_manager.index_is_current(self.index_source)
        self._start_background_indexing(
            self.index_source,
            full_rebuild=full_rebuild,
            status_text=(
                f"Baue Index für {self.index_source.name} neu auf …"
                if full_rebuild
                else f"Prüfe {self.index_source.name} auf neue und geänderte Dateien …"
            ),
        )

    def _start_background_indexing(
        self,
        source: Path,
        full_rebuild: bool,
        status_text: str,
    ):
        if self.index_controller.is_active():
            return
        self.status_bar.set_busy(True)
        self.status_bar.set_text(status_text)
        if self.settings_popup is not None:
            self.settings_popup.set_indexing(True)
        try:
            self.index_controller.start(source, full_rebuild)
        except Exception as exc:
            self.status_bar.set_busy(False)
            if self.settings_popup is not None:
                self.settings_popup.set_indexing(False)
            self.status_bar.set_text(
                f"Indexierung konnte nicht gestartet werden: {exc}"
            )
            QMessageBox.warning(self, "Indexierung", str(exc))

    def on_indexing_progress(self, processed_count: int, current_path: str):
        filename = Path(current_path).name
        self.status_bar.set_text(
            f"Indexierung: {processed_count} Dateien geprüft · {filename}"
        )
        if self.settings_popup is not None:
            self.settings_popup.set_index_progress(processed_count, filename)

    def cancel_background_indexing(self):
        if self.index_controller.is_active():
            self.index_controller.cancel()
            self.status_bar.set_text("Indexierung wird abgebrochen …")

    def check_and_index(self):
        if self.index_controller.is_active():
            state = self.index_controller.current_state()
            source_value = state.get("source")
            if source_value:
                candidate = Path(source_value)
                if candidate != self.index_source:
                    self.pending_index_source = candidate
            self.status_bar.set_busy(True)
            self.status_bar.set_text(
                "Laufende Hintergrundindexierung wieder verbunden …"
            )
            self.index_controller.poll()
            return
        if not self.index_source.exists() or not self.index_source.is_dir():
            self.status_bar.set_text(
                f"Datenquelle nicht erreichbar · verbinde erneut: {self.index_source}"
            )
            self.source_reconnect_timer.start()
            QMessageBox.warning(
                self,
                "Datenquelle nicht erreichbar",
                f"Der Datenordner ist momentan nicht erreichbar:\n{self.index_source}\n\n"
                "PapaGUI versucht die Verbindung automatisch wiederherzustellen. "
                "Sie können in den Einstellungen auch eine andere Quelle auswählen.",
            )
            return
        self.source_reconnect_timer.stop()
        full_rebuild = not self.index_manager.index_is_current(self.index_source)
        self._start_background_indexing(
            self.index_source,
            full_rebuild=full_rebuild,
            status_text=(
                "Erstelle neuen Dokumentindex im Hintergrund …"
                if full_rebuild
                else "Prüfe Datenquelle im Hintergrund auf Änderungen …"
            ),
        )

    def on_index_ready(self, state):
        build_path_value = str(state.get("build_path") or "")
        if not build_path_value:
            return
        self.status_bar.set_text("Index fertig · aktiviere neue Generation …")
        try:
            self._activate_built_index(Path(build_path_value))
            self.index_controller.acknowledge_activation()
        except Exception as exc:
            self.status_bar.set_text(
                "Index fertig · Aktivierung erfolgt nach dem Schließen"
            )
            QMessageBox.warning(
                self,
                "Index wartet auf Aktivierung",
                f"Der neue Index konnte noch nicht aktiviert werden:\n{exc}\n\n"
                "Nach dem Schließen wird der Wechsel erneut versucht.",
            )

    def on_indexing_complete(self, state):
        self.status_bar.set_busy(False)
        if self.settings_popup is not None:
            self.settings_popup.set_indexing(False)
        status = str(state.get("status") or "")
        if status == "cancelled":
            self.status_bar.set_text(
                "Indexierung abgebrochen · bisheriger Index bleibt aktiv"
            )
            self.pending_index_source = None
        elif status == "error":
            error = str(state.get("error") or "Unbekannter Fehler")
            self.status_bar.set_text(f"Indexierung fehlgeschlagen: {error}")
            QMessageBox.warning(self, "Indexierung fehlgeschlagen", error)
            self.pending_index_source = None
        elif status == "no_changes":
            count = int(
                state.get("indexed_count") or state.get("processed_count") or 0
            )
            self._show_customer_recognition_result(state)
            if not state.get("customer_sync_error") and not int(
                state.get("customer_cases_pending") or 0
            ) and not int(state.get("customers_created") or 0) and not int(
                state.get("customers_assigned") or 0
            ):
                self.status_bar.set_text(f"Index aktuell ✓ · {count} Dateien geprüft")
            if int(state.get("customers_created") or 0) or int(
                state.get("customers_assigned") or 0
            ):
                self._refresh_customer_results_only()
        elif status == "completed":
            if state.get("activated_by") == "worker":
                self._reload_active_index()
            if self.pending_index_source is not None:
                self.index_source = self.pending_index_source
                save_index_source(self.index_source)
                self.pending_index_source = None
                self._start_filesystem_monitor()
            elif state.get("source"):
                self.index_source = Path(str(state["source"]))
                save_index_source(self.index_source)
            count = int(state.get("indexed_count") or 0)
            self.navigator.reset("search")
            self._show_initial_customers()
            self.status_bar.set_text(f"Index fertig geladen ✓ · {count} Dateien")
            self._show_customer_recognition_result(state)
            if self.settings_popup is not None:
                self._refresh_settings_popup_data()
        if status in {"completed", "no_changes"} and self.settings_popup is not None:
            if status == "no_changes":
                self._refresh_settings_popup_data()
        if self.pending_filesystem_sync:
            self.pending_filesystem_sync = False
            QTimer.singleShot(0, self._start_incremental_filesystem_sync)

    def _show_customer_recognition_result(self, state: dict):
        error = str(state.get("customer_sync_error") or "")
        pending = int(state.get("customer_cases_pending") or 0)
        created = int(state.get("customers_created") or 0)
        assigned = int(state.get("customers_assigned") or 0)
        if error:
            self.status_bar.set_text(
                f"Index aktiv ✓ · Kundenerkennung fehlgeschlagen: {error}"
            )
            return
        if pending:
            self.status_bar.set_text(
                f"Index aktiv ✓ · {created} Kunden angelegt · {assigned} zugeordnet · "
                f"{pending} Prüffälle offen"
            )
        elif created or assigned:
            self.status_bar.set_text(
                f"Index aktiv ✓ · {created} Kunden angelegt · {assigned} zugeordnet"
            )

    def _activate_built_index(self, build_path: Path):
        self.search_generation += 1
        self._cancel_outdated_searches()
        for search_worker in tuple(self.search_workers):
            search_worker.wait()
        self.index_manager.close()
        try:
            activate_index(DB_FILE, build_path)
        finally:
            self.index_manager = IndexManager(
                DB_FILE,
                options=self.index_options,
            )
        self._refresh_search_facets()

    def _reload_active_index(self):
        self.search_generation += 1
        self._cancel_outdated_searches()
        for search_worker in tuple(self.search_workers):
            search_worker.wait()
        self.index_manager.close()
        self.index_manager = IndexManager(DB_FILE, options=self.index_options)
        self._refresh_search_facets()

    def _start_filesystem_monitor(self):
        if self.filesystem_monitor is not None:
            self.filesystem_monitor.requestInterruption()
            self.filesystem_monitor.wait()
            self.filesystem_monitor.deleteLater()
        self.filesystem_monitor = None
        if not self.index_source.exists() or not self.index_source.is_dir():
            return
        self.filesystem_monitor = FileSystemMonitor(
            self.index_source,
            excluded_folders=self.index_options.excluded_folder_names,
            parent=self,
        )
        self.filesystem_monitor.changesDetected.connect(
            self._on_filesystem_changes
        )
        self.filesystem_monitor.scanFailed.connect(
            lambda error: self.status_bar.set_text(
                f"Dateiüberwachung: {error}"
            )
        )
        self.filesystem_monitor.start()

    def _on_filesystem_changes(self, changes):
        if self.index_controller.is_active():
            self.pending_filesystem_sync = True
            return
        self.status_bar.set_text(
            f"{changes.total} Dateiänderungen erkannt · Index wird aktualisiert …"
        )
        self._start_incremental_filesystem_sync()

    def _start_incremental_filesystem_sync(self):
        if self.index_controller.is_active():
            self.pending_filesystem_sync = True
            return
        self._start_background_indexing(
            self.index_source,
            full_rebuild=False,
            status_text="Aktualisiere Index nach Dateiänderungen …",
        )

    def on_index_options_changed(self, options):
        self.index_options = options
        self.index_manager.options = options
        save_index_options(options)
        self.status_bar.set_text("Indexeinstellungen gespeichert")
        self._start_filesystem_monitor()

    def on_load_backup_requested(self, backup_path_value: str):
        if self.index_controller.is_active():
            QMessageBox.information(
                self,
                "Indexierung läuft",
                "Bitte die laufende Indexierung zuerst abschließen.",
            )
            return
        try:
            build_path = create_restore_build(
                DB_FILE,
                Path(backup_path_value),
            )
            metadata = validate_index(build_path)
            self._activate_built_index(build_path)
            restored_root = metadata.get("index_root", "")
            if restored_root:
                self.index_source = Path(restored_root)
                save_index_source(self.index_source)
            self.navigator.reset("search")
            self._show_initial_customers()
            self.status_bar.set_text("Alter Index wurde geladen ✓")
            if self.settings_popup is not None:
                self.settings_popup.data_path_input.setText(
                    str(self.index_source)
                )
                self._refresh_settings_popup_data()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Index konnte nicht geladen werden",
                str(exc),
            )

    def closeEvent(self, event):
        self.initialization_timer.stop()
        self.search_debounce.stop()
        for worker in tuple(self.search_workers):
            worker.requestInterruption()
        for worker in tuple(self.search_workers):
            worker.wait()
        if (
            self.filesystem_monitor is not None
            and self.filesystem_monitor.isRunning()
        ):
            self.filesystem_monitor.requestInterruption()
            self.filesystem_monitor.wait()
        if (
            self.settings_data_worker is not None
            and self.settings_data_worker.isRunning()
        ):
            self.settings_data_worker.requestInterruption()
            self.settings_data_worker.wait()
        self.folder_page.cleanup()
        try:
            DocumentConverter.clear_word_preview_cache()
        except Exception:
            LOGGER.exception("Word-Preview-Cache konnte nicht gelöscht werden.")
        self.index_manager.close()
        self.customer_repository.close()
        self.index_controller.release_owner()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
