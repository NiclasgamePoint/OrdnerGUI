import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QSplitter,
    QMessageBox, QProgressBar, QToolButton, QCompleter, QScrollArea
)
from PySide6.QtCore import Qt, QSize, QTimer, QStringListModel
from PySide6.QtGui import QIcon
from datetime import datetime

from app.core.config import (
    WINDOW_TITLE,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    DB_FILE,
    CUSTOMER_DB_FILE,
    get_configured_index_source,
    load_index_options,
    save_index_options,
    save_index_source,
)
from app.core.index_manager import IndexManager
from app.core.customer_repository import CustomerRepository
from app.core.index_diagnostics import IndexDiagnosticsService
from app.core.search_models import SearchFilters, SearchHistory
from app.core.index_store import (
    activate_index,
    available_backups,
    create_restore_build,
    validate_index,
)
from app.gui.viewer import FileViewer
from app.gui.theme import ThemeManager
from app.gui.settings_popup import SettingsPopup
from app.gui.panels import CustomerDetailsPanel, CustomerOverviewPanel
from app.gui.widgets import (
    AppButton,
    HighlightDelegate,
    SearchFilterPopup,
    SearchResultSection,
)
from app.gui.workers import IndexJobController, SearchWorker
from app.services import FileSystemMonitor


class MainWindow(QMainWindow):
    RESULT_KIND_ROLE = Qt.UserRole
    RESULT_VALUE_ROLE = Qt.UserRole + 1

    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setGeometry(100, 100, WINDOW_WIDTH, WINDOW_HEIGHT)
        
        self.index_options = load_index_options()
        self.index_controller = IndexJobController(DB_FILE, parent=self)
        self.index_controller.adopt_running_job()
        self.index_manager = IndexManager(DB_FILE, options=self.index_options)
        self.customer_repository = CustomerRepository(CUSTOMER_DB_FILE)
        self.current_customer = None
        self.index_source = get_configured_index_source()
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
        self.pending_index_source = None
        self.theme_manager = ThemeManager()
        self.diagnostics_service = IndexDiagnosticsService()
        self.settings_popup = None
        self.search_generation = 0
        self.search_workers = set()
        self.search_counts = {"folders": None, "files": None, "text": None}
        self.search_pages = {"folders": 1, "files": 1, "text": 1}
        self.search_page_size = 25
        self.search_history = SearchHistory()
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(250)
        self.search_debounce.timeout.connect(self._start_live_folder_search)
        self.filesystem_monitor = None
        self.pending_filesystem_sync = False
        
        self.init_ui()
        self.index_controller.progress.connect(self.on_indexing_progress)
        self.index_controller.ready.connect(self.on_index_ready)
        self.index_controller.finished.connect(self.on_indexing_complete)
        self._refresh_search_facets()
        self.apply_theme()
        self.check_and_index()
        self._start_filesystem_monitor()
    
    def init_ui(self):
        main_widget = QWidget()
        main_widget.setObjectName("RootWidget")
        main_root_layout = QVBoxLayout(main_widget)
        main_root_layout.setContentsMargins(0, 0, 0, 0)
        main_root_layout.setSpacing(0)

        self.page_scroll_area = QScrollArea()
        self.page_scroll_area.setWidgetResizable(True)
        self.page_scroll_area.setObjectName("MainScrollArea")
        main_root_layout.addWidget(self.page_scroll_area)

        page_widget = QWidget()
        page_widget.setObjectName("RootWidget")
        self.page_scroll_area.setWidget(page_widget)

        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)

        search_card = QWidget()
        search_card.setObjectName("SearchCard")
        search_card.setFixedHeight(50)
        search_layout = QHBoxLayout(search_card)
        search_layout.setContentsMargins(12, 7, 12, 7)
        search_layout.setSpacing(8)
        
        search_label = QLabel("Suche:")
        search_label.setObjectName("StatCaption")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Kunde, Ordner, Dateiname oder Dokumentinhalt durchsuchen ..."
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        self.search_input.returnPressed.connect(self.start_full_search)
        self.history_model = QStringListModel(self.search_history.entries(), self)
        self.search_completer = QCompleter(self.history_model, self)
        self.search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.search_completer.setFilterMode(Qt.MatchContains)
        self.search_input.setCompleter(self.search_completer)

        self.search_button = AppButton("Suchen")
        self.search_button.setObjectName("SearchButton")
        self.search_button.setMinimumWidth(120)
        self.search_button.clicked.connect(self.start_full_search)

        self.filter_popup = SearchFilterPopup(self)
        self.filter_popup.filtersChanged.connect(self._on_filter_changed)
        self.domain_filter = self.filter_popup.domain_combo
        self.year_filter = self.filter_popup.year_combo
        self.file_type_filter = self.filter_popup.file_type_combo

        self.filter_button = AppButton("Filter", AppButton.SECONDARY)
        self.filter_button.setMinimumWidth(90)
        self.filter_button.clicked.connect(self.open_filter_popup)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("SettingsButton")
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.setIconSize(QSize(16, 16))
        icon = QIcon.fromTheme("preferences-system")
        if icon.isNull():
            self.settings_button.setText("⚙")
        else:
            self.settings_button.setIcon(icon)
        self.settings_button.setToolTip("Einstellungen öffnen")
        self.settings_button.clicked.connect(self.open_settings_popup)
        
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input, 2)
        search_layout.addWidget(self.search_button)
        search_layout.addWidget(self.filter_button)
        search_layout.addStretch(1)
        search_layout.addWidget(self.settings_button)
        main_layout.addWidget(search_card)
        
        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        content_splitter.setMinimumHeight(620)
        
        left_card = QWidget()
        left_card.setObjectName("SideCard")
        left_card.setMinimumWidth(320)
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_title = QLabel("Suchergebnisse")
        left_title.setObjectName("StatValue")
        left_layout.addWidget(left_title)
        self._create_result_groups(left_layout)

        details_card = QWidget()
        details_card.setObjectName("DetailsCard")
        details_outer_layout = QVBoxLayout(details_card)
        details_outer_layout.setContentsMargins(10, 10, 10, 10)

        self.details_splitter = QSplitter(Qt.Horizontal)
        self.details_splitter.setObjectName("DetailsSplitter")
        self.details_splitter.setChildrenCollapsible(False)

        details_widget = CustomerDetailsPanel(self.customer_repository)
        self.customer_details_panel = details_widget
        self.customer_name_label = details_widget.customer_name_label
        self.file_count_label = details_widget.file_count_label
        self.size_label = details_widget.size_label
        self.modified_label = details_widget.modified_label
        self.service_types_label = details_widget.service_types_label
        self.file_list = details_widget.file_list

        viewer_widget = QWidget()
        viewer_widget.setObjectName("FileViewerSection")
        viewer_widget.setMinimumWidth(360)
        viewer_layout = QVBoxLayout(viewer_widget)
        viewer_layout.setContentsMargins(14, 12, 14, 14)
        viewer_layout.setSpacing(8)

        viewer_title = QLabel("Datei-Viewer")
        viewer_title.setObjectName("SectionTitle")
        viewer_layout.addWidget(viewer_title)

        self.file_viewer = FileViewer()
        self.customer_details_panel.fileActivated.connect(
            lambda path: self.file_viewer.open_file(Path(path))
        )
        viewer_layout.addWidget(self.file_viewer)

        self.details_splitter.addWidget(details_widget)
        self.details_splitter.addWidget(viewer_widget)
        self.details_splitter.setStretchFactor(0, 2)
        self.details_splitter.setStretchFactor(1, 3)
        self.details_splitter.setSizes([400, 600])
        details_outer_layout.addWidget(self.details_splitter)
        
        content_splitter.addWidget(left_card)
        content_splitter.addWidget(details_card)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 3)
        content_splitter.setSizes([340, 1040])
        
        main_layout.addWidget(content_splitter, 1)
        
        status_card = QWidget()
        status_card.setObjectName("StatusCard")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        self.status_label = QLabel("Bereit...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        
        main_layout.addWidget(status_card)

        overview_card = QWidget()
        overview_card.setObjectName("DetailsCard")
        overview_card.setMinimumHeight(360)
        overview_layout = QVBoxLayout(overview_card)
        overview_layout.setContentsMargins(12, 12, 12, 12)
        overview_layout.setSpacing(8)
        overview_title = QLabel("Kundenübersicht")
        overview_title.setObjectName("SectionTitle")
        overview_layout.addWidget(overview_title)

        self.customer_overview_panel = CustomerOverviewPanel(self.customer_repository)
        self.customer_overview_panel.setMinimumHeight(320)
        overview_layout.addWidget(self.customer_overview_panel, 1)
        main_layout.addWidget(overview_card)

        self.customer_details_panel.customerChanged.connect(self.customer_overview_panel.refresh)
        self.customer_overview_panel.customerChanged.connect(self.customer_details_panel._refresh_customer_summary)

        page_widget.setLayout(main_layout)

    def apply_theme(self):
        app = QApplication.instance()
        if app is not None:
            self.theme_manager.apply(app)

    def open_filter_popup(self):
        if self.filter_popup.isVisible():
            self.filter_popup.close()
            return
        self.filter_popup.adjustSize()
        anchor = self.filter_button.mapToGlobal(self.filter_button.rect().bottomLeft())
        available = self.screen().availableGeometry()
        popup_x = min(anchor.x(), available.right() - self.filter_popup.width())
        popup_y = anchor.y() + 6
        if popup_y + self.filter_popup.height() > available.bottom():
            button_top = self.filter_button.mapToGlobal(self.filter_button.rect().topLeft()).y()
            popup_y = button_top - self.filter_popup.height() - 6
        self.filter_popup.move(max(available.left(), popup_x), max(available.top(), popup_y))
        self.filter_popup.show()
        self.filter_popup.raise_()

    def open_settings_popup(self):
        self.filter_popup.close()
        if self.settings_popup is not None and self.settings_popup.isVisible():
            self.settings_popup.close()
            return

        self.settings_popup = SettingsPopup(
            self.theme_manager.mode,
            self.theme_manager.accent,
            self,
            data_path=self.index_source,
            indexing=self.index_controller.is_active(),
            backups=available_backups(DB_FILE),
            index_options=self.index_options,
            diagnostics=self.diagnostics_service.inspect(DB_FILE),
        )
        self.settings_popup.appearanceChanged.connect(self.on_settings_appearance_changed)
        self.settings_popup.dataPathChanged.connect(self.on_settings_data_path_changed)
        self.settings_popup.reindexRequested.connect(self.on_settings_reindex_requested)
        self.settings_popup.cancelIndexRequested.connect(self.cancel_background_indexing)
        self.settings_popup.loadBackupRequested.connect(self.on_load_backup_requested)
        self.settings_popup.indexOptionsChanged.connect(self.on_index_options_changed)
        self.settings_popup.destroyed.connect(self._clear_settings_popup)
        self.settings_popup.resize(self.settings_popup.size_for_parent())

        self._center_settings_popup()
        self.settings_popup.show()
        self.settings_popup.raise_()

    def _center_settings_popup(self):
        if self.settings_popup is None:
            return

        center = self.mapToGlobal(self.rect().center())
        popup_x = center.x() - (self.settings_popup.width() // 2)
        popup_y = center.y() - (self.settings_popup.height() // 2)
        self.settings_popup.move(popup_x, popup_y)

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

    def on_settings_appearance_changed(self, mode: str, accent: str):
        self.theme_manager.set_mode(mode)
        self.theme_manager.set_accent(accent)
        self.theme_manager.save()
        self.apply_theme()

    def on_settings_data_path_changed(self, path_value: str):
        new_source = Path(path_value).expanduser().resolve()
        if not new_source.exists() or not new_source.is_dir():
            QMessageBox.warning(self, "Datenquelle", "Der ausgewählte Datenordner ist ungültig.")
            return
        if self.index_controller.is_active():
            QMessageBox.information(
                self,
                "Indexierung läuft",
                "Bitte warten, bis die aktuelle Indexierung abgeschlossen ist.",
            )
            return
        if new_source == self.index_source and self.index_manager.has_index_for_root(new_source):
            save_index_source(new_source)
            self.status_label.setText(f"Datenquelle aktiv ✓ ({new_source.name})")
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
            QMessageBox.warning(self, "Indexierung", "Die konfigurierte Datenquelle existiert nicht.")
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
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.status_label.setText(status_text)
        if self.settings_popup is not None:
            self.settings_popup.set_indexing(True)
        try:
            self.index_controller.start(source, full_rebuild)
        except Exception as exc:
            self.progress_bar.setVisible(False)
            if self.settings_popup is not None:
                self.settings_popup.set_indexing(False)
            self.status_label.setText(f"Indexierung konnte nicht gestartet werden: {exc}")
            QMessageBox.warning(self, "Indexierung", str(exc))

    def on_indexing_progress(self, processed_count: int, current_path: str):
        filename = Path(current_path).name
        self.status_label.setText(f"Indexierung im Hintergrund: {processed_count} Dateien · {filename}")
        if self.settings_popup is not None:
            self.settings_popup.set_index_progress(processed_count, filename)

    def cancel_background_indexing(self):
        if self.index_controller.is_active():
            self.index_controller.cancel()
            self.status_label.setText("Indexierung wird abgebrochen …")
    
    def check_and_index(self):
        if self.index_controller.is_active():
            state = self.index_controller.current_state()
            source_value = state.get("source")
            if source_value:
                candidate = Path(source_value)
                if candidate != self.index_source:
                    self.pending_index_source = candidate
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setVisible(True)
            self.status_label.setText("Laufende Hintergrundindexierung wieder verbunden …")
            self.index_controller.poll()
            return
        if not self.index_source.exists():
            self.status_label.setText(f"Indexquelle nicht gefunden: {self.index_source}")
            QMessageBox.warning(
                self,
                "Indexquelle fehlt",
                f"Der Datenordner wurde nicht gefunden:\n{self.index_source}\n\n"
                "Bitte den Ordner anlegen oder den Pfad in app/core/config.py anpassen."
            )
            return

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
        self.status_label.setText("Index ist fertig · aktiviere neue Generation …")
        try:
            self._activate_built_index(Path(build_path_value))
            self.index_controller.acknowledge_activation()
        except Exception as exc:
            self.status_label.setText(
                "Index fertig · Aktivierung erfolgt nach dem Schließen des Programms"
            )
            QMessageBox.warning(
                self,
                "Index wartet auf Aktivierung",
                f"Der neue Index ist fertig, konnte aber noch nicht aktiviert werden:\n{exc}\n\n"
                "Nach dem Schließen des Programms wird der Wechsel automatisch erneut versucht.",
            )

    def on_indexing_complete(self, state):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        if self.settings_popup is not None:
            self.settings_popup.set_indexing(False)
        status = str(state.get("status") or "")
        if status == "cancelled":
            self.status_label.setText("Indexierung abgebrochen · bisheriger Index bleibt aktiv")
            self.pending_index_source = None
        elif status == "error":
            error = str(state.get("error") or "Unbekannter Fehler")
            self.status_label.setText(f"Indexierung fehlgeschlagen: {error}")
            QMessageBox.warning(self, "Indexierung fehlgeschlagen", error)
            self.pending_index_source = None
        elif status == "no_changes":
            count = int(state.get("indexed_count") or state.get("processed_count") or 0)
            self.status_label.setText(f"Index aktuell ✓ ({count} Dateien geprüft)")
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
            self._reset_search_results()
            self.current_customer = None
            self.file_list.clear()
            self.status_label.setText(f"Index fertig geladen ✓ ({count} Dateien)")
            if self.settings_popup is not None:
                self.settings_popup.set_backups(available_backups(DB_FILE))
                self.settings_popup.set_diagnostics(self.diagnostics_service.inspect(DB_FILE))
        if self.pending_filesystem_sync:
            self.pending_filesystem_sync = False
            QTimer.singleShot(0, self._start_incremental_filesystem_sync)

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
        self.filesystem_monitor.changesDetected.connect(self._on_filesystem_changes)
        self.filesystem_monitor.scanFailed.connect(
            lambda error: self.status_label.setText(f"Dateiüberwachung: {error}")
        )
        self.filesystem_monitor.start()

    def _on_filesystem_changes(self, changes):
        if self.index_controller.is_active():
            self.pending_filesystem_sync = True
            return
        self.status_label.setText(
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

    def _activate_built_index(self, build_path: Path):
        if build_path is None:
            return
        self.search_generation += 1
        self._cancel_outdated_searches()
        for search_worker in tuple(self.search_workers):
            search_worker.wait()
        self.index_manager.close()
        try:
            activate_index(DB_FILE, build_path)
        finally:
            self.index_manager = IndexManager(DB_FILE, options=self.index_options)
        self._refresh_search_facets()

    def _reload_active_index(self):
        self.search_generation += 1
        self._cancel_outdated_searches()
        for search_worker in tuple(self.search_workers):
            search_worker.wait()
        self.index_manager.close()
        self.index_manager = IndexManager(DB_FILE, options=self.index_options)
        self._refresh_search_facets()

    def on_index_options_changed(self, options):
        self.index_options = options
        self.index_manager.options = options
        save_index_options(options)
        self.status_label.setText("Indexeinstellungen gespeichert")
        self._start_filesystem_monitor()

    def on_load_backup_requested(self, backup_path_value: str):
        if self.index_controller.is_active():
            QMessageBox.information(
                self, "Indexierung läuft", "Bitte die laufende Indexierung zuerst abschließen."
            )
            return
        try:
            build_path = create_restore_build(DB_FILE, Path(backup_path_value))
            metadata = validate_index(build_path)
            self._activate_built_index(build_path)
            restored_root = metadata.get("index_root", "")
            if restored_root:
                self.index_source = Path(restored_root)
                save_index_source(self.index_source)
            self._reset_search_results()
            self.file_list.clear()
            self.status_label.setText("Alter Index wurde geladen ✓")
            if self.settings_popup is not None:
                self.settings_popup.data_path_input.setText(str(self.index_source))
                self.settings_popup.set_backups(available_backups(DB_FILE))
        except Exception as exc:
            QMessageBox.warning(self, "Index konnte nicht geladen werden", str(exc))
    
    def _create_result_groups(self, parent_layout: QVBoxLayout):
        self.result_groups = {}
        self.result_sections = {}
        self.result_group_titles = {}
        results_splitter = QSplitter(Qt.Vertical)
        results_splitter.setObjectName("ResultsSplitter")
        results_splitter.setChildrenCollapsible(False)

        for category, title in (
            ("folders", "Ordner"),
            ("files", "Dateinamen"),
            ("text", "Dokumentinhalte"),
        ):
            section = SearchResultSection(title)
            section.setObjectName("ResultSection")
            result_list = section.list_widget
            result_list.itemClicked.connect(self.on_search_result_clicked)
            result_list.itemDoubleClicked.connect(self.on_search_result_double_clicked)
            section.pageRequested.connect(
                lambda page, category=category: self._request_result_page(category, page)
            )

            results_splitter.addWidget(section)
            self.result_groups[category] = result_list
            self.result_sections[category] = section
            self.result_group_titles[category] = (section.heading, title)

        results_splitter.setSizes([200, 200, 200])
        parent_layout.addWidget(results_splitter)
        self._reset_search_results()

    def _replace_group_items(self, category: str, items):
        result_list = self.result_groups[category]
        result_list.clear()
        for item in items:
            result_list.addItem(item)

    def _placeholder_item(self, text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
        return item

    def _reset_search_results(self):
        self._replace_group_items("folders", [self._placeholder_item("Suchbegriff eingeben")])
        enter_hint = self._placeholder_item("Mit Enter oder ‚Suchen‘ starten")
        self._replace_group_items("files", [enter_hint])
        self._replace_group_items(
            "text", [self._placeholder_item("Mit Enter oder ‚Suchen‘ starten")]
        )
        self.search_counts = {"folders": None, "files": None, "text": None}
        self.search_pages = {"folders": 1, "files": 1, "text": 1}
        for section in self.result_sections.values():
            section.reset_title()
        self._refresh_result_group_titles()

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
            selected_index = combo.findData(selected)
            combo.setCurrentIndex(max(0, selected_index))
            combo.blockSignals(False)
        self._update_filter_button()

    def _on_filter_changed(self):
        self._update_filter_button()
        if self.search_input.text().strip():
            self.on_search_text_changed(self.search_input.text())

    def _update_filter_button(self):
        active_count = self.filter_popup.active_filter_count()
        self.filter_button.setText(
            "Filter" if active_count == 0 else f"Filter ({active_count})"
        )
        self.filter_button.setProperty("filtersActive", active_count > 0)
        self.filter_button.style().unpolish(self.filter_button)
        self.filter_button.style().polish(self.filter_button)

    def _clear_search_filters(self):
        self.filter_popup.clear_filters()

    def on_search_text_changed(self, text: str):
        self.search_generation += 1
        self._cancel_outdated_searches()
        self.search_debounce.stop()
        query = text.strip()
        self.search_counts = {"folders": None, "files": None, "text": None}
        self.search_pages = {"folders": 1, "files": 1, "text": 1}
        self._refresh_result_group_titles()

        if not query:
            self._reset_search_results()
            self.status_label.setText("Bereit ...")
            return

        self._replace_group_items("folders", [self._placeholder_item("Suche läuft …")])
        self._replace_group_items(
            "files", [self._placeholder_item("Mit Enter oder ‚Suchen‘ starten")]
        )
        self._replace_group_items(
            "text", [self._placeholder_item("Mit Enter oder ‚Suchen‘ starten")]
        )
        self.status_label.setText("Ordner- und Kundensuche wird vorbereitet …")
        self.search_debounce.start()

    def _start_live_folder_search(self):
        query = self.search_input.text().strip()
        if query:
            self._launch_search("folders", query, self.search_generation)

    def start_full_search(self):
        query = self.search_input.text().strip()
        if len(query) < 2:
            self.status_label.setText("Bitte mindestens 2 Zeichen für die vollständige Suche eingeben.")
            return

        self.search_debounce.stop()
        self.search_generation += 1
        self._cancel_outdated_searches()
        generation = self.search_generation
        self.history_model.setStringList(self.search_history.add(query))
        self.current_customer = None
        self.search_counts = {"folders": None, "files": None, "text": None}
        self.search_pages = {"folders": 1, "files": 1, "text": 1}
        self._refresh_result_group_titles()

        for category in self.result_groups:
            self._replace_group_items(category, [self._placeholder_item("Suche läuft …")])

        self.status_label.setText("Durchsuche Ordner, Dateinamen und Dokumentinhalte parallel …")
        for category in ("folders", "files", "text"):
            self._launch_search(category, query, generation, page=1)

    def _launch_search(self, category: str, query: str, generation: int, page: int = 1):
        worker = SearchWorker(
            DB_FILE,
            generation,
            category,
            query,
            self.index_options.result_limit,
            self._current_search_filters(),
            page,
            self.search_page_size,
            CUSTOMER_DB_FILE,
        )
        worker.completed.connect(self._on_search_completed)
        worker.finished.connect(lambda worker=worker: self._release_search_worker(worker))
        self.search_workers.add(worker)
        worker.start()

    def _request_result_page(self, category: str, page: int):
        section = self.result_sections[category]
        if page < 1 or page > section.page_count:
            return
        query = self.search_input.text().strip()
        if not query:
            return
        self.search_pages[category] = page
        self._replace_group_items(category, [self._placeholder_item("Seite wird geladen …")])
        self._launch_search(category, query, self.search_generation, page=page)

    def _cancel_outdated_searches(self):
        for worker in tuple(self.search_workers):
            worker.requestInterruption()

    def _release_search_worker(self, worker: SearchWorker):
        self.search_workers.discard(worker)
        worker.deleteLater()

    def _on_search_completed(self, generation: int, category: str, results, error: str):
        if generation != self.search_generation:
            return

        if error:
            item = self._placeholder_item(f"Fehler: {error}")
            item.setToolTip(error)
            self._replace_group_items(category, [item])
            self.search_counts[category] = 0
        else:
            page = results
            self.search_pages[category] = page.page
            self.search_counts[category] = page.total
            self.result_sections[category].set_page(page.page, page.page_count, page.total)
            if category == "folders":
                self._show_folder_results(page.items)
            elif category == "files":
                self._show_file_results(page.items)
            elif category == "text":
                self._show_text_results(page.items)
        self._update_search_status()

    def _show_folder_results(self, results):
        items = []
        for result in results[: self.index_options.result_limit]:
            name = result.get("folder_name") or "Unbekannt"
            file_count = int(result.get("file_count") or 0)
            relative_path = result.get("relative_path") or ""
            suffix = f"  ·  {relative_path}" if relative_path and relative_path != name else ""
            if result.get("customer_match"):
                suffix = f"  ·  Kunde · {relative_path}"
            item = QListWidgetItem(f"{name}  ·  {file_count} Dateien{suffix}")
            item.setData(self.RESULT_KIND_ROLE, "folder")
            item.setData(self.RESULT_VALUE_ROLE, result["folder_path"])
            item.setToolTip(result["folder_path"])
            item.setData(HighlightDelegate.QUERY_ROLE, self.search_input.text().strip())
            items.append(item)
        if not items:
            items.append(self._placeholder_item("Keine Ordner gefunden"))
        self._replace_group_items("folders", items)

    def _show_file_results(self, results):
        items = []
        for file_info in results[:100]:
            relative_dir = file_info.get("relative_dir") or ""
            suffix = f"  ·  {relative_dir}" if relative_dir else ""
            item = QListWidgetItem(f"{file_info['filename']}{suffix}")
            item.setData(self.RESULT_KIND_ROLE, "file")
            item.setData(self.RESULT_VALUE_ROLE, file_info["path"])
            item.setToolTip(file_info["path"])
            item.setData(HighlightDelegate.QUERY_ROLE, self.search_input.text().strip())
            items.append(item)
        if not items:
            items.append(self._placeholder_item("Keine Dateinamen gefunden"))
        self._replace_group_items("files", items)

    def _show_text_results(self, results):
        items = []
        for result in results[:100]:
            filepath = Path(result["path"])
            line_num = result.get("line")
            location = f"Zeile {line_num}" if line_num else "Dokumentinhalt"
            excerpt = result.get("excerpt") or ""
            suffix = f"  ·  {excerpt}" if excerpt else ""
            item = QListWidgetItem(f"{filepath.name}  ·  {location}{suffix}")
            item.setData(self.RESULT_KIND_ROLE, "text")
            item.setData(self.RESULT_VALUE_ROLE, str(filepath))
            tooltip = str(filepath)
            if excerpt:
                tooltip += f"\n\n{excerpt}"
            item.setToolTip(tooltip)
            item.setData(HighlightDelegate.QUERY_ROLE, self.search_input.text().strip())
            items.append(item)
        if not items:
            items.append(self._placeholder_item("Keine Texttreffer gefunden"))
        self._replace_group_items("text", items)

    def _update_search_status(self):
        labels = {"folders": "Ordner", "files": "Dateien", "text": "Text"}
        parts = []
        for category in ("folders", "files", "text"):
            count = self.search_counts[category]
            parts.append(f"{labels[category]}: {'…' if count is None else count}")
        self.status_label.setText("  ·  ".join(parts))
        self._refresh_result_group_titles()

    def _refresh_result_group_titles(self):
        for category, (heading, base_title) in self.result_group_titles.items():
            count = self.search_counts[category]
            if count is None:
                heading.setText(base_title)

    def on_search_result_clicked(self, item: QListWidgetItem):
        if item.data(self.RESULT_KIND_ROLE) == "folder":
            self.show_folder_details(item.data(self.RESULT_VALUE_ROLE))

    def on_search_result_double_clicked(self, item: QListWidgetItem):
        if item.data(self.RESULT_KIND_ROLE) in {"file", "text"}:
            filepath = item.data(self.RESULT_VALUE_ROLE)
            if filepath:
                self.file_viewer.open_file(Path(filepath))

    def show_customer_details(self, customer_name: str):
        self.current_customer = customer_name
        
        details = self.index_manager.get_customer_details(customer_name)
        
        self.customer_name_label.setText(customer_name)
        self.file_count_label.setText(str(details['file_count']))
        
        size_mb = details['total_size'] / 1024 / 1024
        self.size_label.setText(f"{size_mb:.2f} MB")
        
        if details['last_modified']:
            mod_date = datetime.fromisoformat(details['last_modified']).strftime("%d.%m.%Y %H:%M")
            self.modified_label.setText(mod_date)
        
        self.service_types_label.setText(", ".join(details['service_types']))
        
        self.file_list.clear()
        for file_info in details['files']:
            item = QListWidgetItem(file_info['filename'])
            item.setData(Qt.UserRole, file_info['path'])
            item.setToolTip(file_info['path'])
            self.file_list.addItem(item)
        
        self.status_label.setText(f"✓ Kunde: {customer_name} mit {details['file_count']} Dateien")

    def show_folder_details(self, folder_path: str):
        self.current_customer = None
        details = self.index_manager.get_folder_details(folder_path)
        self.customer_details_panel.set_folder(details)
        self.status_label.setText(
            f"✓ Ordner: {details['folder_name']} mit {details['file_count']} Dateien"
        )
    
    def on_file_selected(self, item: QListWidgetItem):
        filepath = item.data(Qt.UserRole)
        if filepath:
            self.file_viewer.open_file(Path(filepath))
    
    def closeEvent(self, event):
        self.search_debounce.stop()
        for worker in tuple(self.search_workers):
            worker.requestInterruption()
        for worker in tuple(self.search_workers):
            worker.wait()
        if self.filesystem_monitor is not None and self.filesystem_monitor.isRunning():
            self.filesystem_monitor.requestInterruption()
            self.filesystem_monitor.wait()
        self.index_manager.close()
        self.customer_repository.close()
        self.index_controller.release_owner()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
