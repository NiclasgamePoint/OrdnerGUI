import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QSplitter,
    QMessageBox, QProgressBar, QToolButton
)
from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QIcon
from datetime import datetime

from app.core.config import (
    WINDOW_TITLE,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    DB_FILE,
    get_configured_index_source,
    save_index_source,
)
from app.core.index_manager import IndexManager
from app.gui.viewer import FileViewer
from app.gui.theme import ThemeManager
from app.gui.settings_popup import SettingsPopup
from app.gui.widgets import AppButton


class IndexingWorker(QThread):
    progress = Signal(int)
    
    def __init__(self, db_path: Path, base_path: Path, replace_existing: bool = False):
        super().__init__()
        self.db_path = db_path
        self.base_path = base_path
        self.replace_existing = replace_existing
        self.error = ""
        self.indexed_count = 0
    
    def run(self):
        manager = IndexManager(self.db_path)
        try:
            self.indexed_count = manager.index_directory(
                self.base_path,
                replace_existing=self.replace_existing,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:
            self.error = str(exc)
        finally:
            manager.close()


class SearchWorker(QThread):
    """Run one search type with its own thread-local database connection."""

    completed = Signal(int, str, object, str)

    def __init__(self, db_path: Path, generation: int, category: str, query: str):
        super().__init__()
        self.db_path = db_path
        self.generation = generation
        self.category = category
        self.query = query

    def run(self):
        manager = None
        try:
            manager = IndexManager(self.db_path, initialize=False)
            if self.category == "folders":
                results = manager.search_customers(self.query, limit=200)
            elif self.category == "files":
                results = manager.search_files(self.query, limit=200)
            elif self.category == "text":
                results = manager.search_in_text(
                    self.query,
                    should_cancel=self.isInterruptionRequested,
                )
            else:
                raise ValueError(f"Unbekannte Suchkategorie: {self.category}")
            self.completed.emit(self.generation, self.category, results, "")
        except Exception as exc:
            self.completed.emit(self.generation, self.category, [], str(exc))
        finally:
            if manager is not None:
                manager.close()


class MainWindow(QMainWindow):
    RESULT_KIND_ROLE = Qt.UserRole
    RESULT_VALUE_ROLE = Qt.UserRole + 1

    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setGeometry(100, 100, WINDOW_WIDTH, WINDOW_HEIGHT)
        
        self.index_manager = IndexManager(DB_FILE)
        self.current_customer = None
        self.index_worker = None
        self.index_source = get_configured_index_source()
        self.pending_index_source = None
        self.theme_manager = ThemeManager()
        self.settings_popup = None
        self.search_generation = 0
        self.search_workers = set()
        self.search_counts = {"folders": None, "files": None, "text": None}
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(250)
        self.search_debounce.timeout.connect(self._start_live_folder_search)
        
        self.init_ui()
        self.apply_theme()
        self.check_and_index()
    
    def init_ui(self):
        main_widget = QWidget()
        main_widget.setObjectName("RootWidget")
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

        self.search_button = AppButton("Suchen")
        self.search_button.setObjectName("SearchButton")
        self.search_button.setMinimumWidth(120)
        self.search_button.clicked.connect(self.start_full_search)

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
        search_layout.addStretch(1)
        search_layout.addWidget(self.settings_button)
        
        main_layout.addWidget(search_card)
        
        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        
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

        details_widget = QWidget()
        details_widget.setObjectName("CustomerDetailsSection")
        details_widget.setMinimumWidth(280)
        
        details_layout = QVBoxLayout(details_widget)
        details_layout.setContentsMargins(14, 12, 14, 14)
        details_layout.setSpacing(10)

        details_title = QLabel("Kundendetails")
        details_title.setObjectName("SectionTitle")
        details_layout.addWidget(details_title)
        
        info_layout = QGridLayout()
        info_layout.setHorizontalSpacing(18)
        info_layout.setVerticalSpacing(3)

        name_caption = QLabel("Ordner")
        name_caption.setObjectName("StatCaption")
        info_layout.addWidget(name_caption, 0, 0, 1, 2)
        self.customer_name_label = QLabel("-")
        self.customer_name_label.setObjectName("StatValue")
        self.customer_name_label.setWordWrap(True)
        info_layout.addWidget(self.customer_name_label, 1, 0, 1, 2)
        
        files_caption = QLabel("Dateien")
        files_caption.setObjectName("StatCaption")
        info_layout.addWidget(files_caption, 2, 0)
        self.file_count_label = QLabel("0")
        self.file_count_label.setObjectName("StatValue")
        info_layout.addWidget(self.file_count_label, 3, 0)
        
        size_caption = QLabel("Größe")
        size_caption.setObjectName("StatCaption")
        info_layout.addWidget(size_caption, 2, 1)
        self.size_label = QLabel("0 B")
        self.size_label.setObjectName("StatValue")
        info_layout.addWidget(self.size_label, 3, 1)
        
        modified_caption = QLabel("Zuletzt geändert")
        modified_caption.setObjectName("StatCaption")
        info_layout.addWidget(modified_caption, 4, 0, 1, 2)
        self.modified_label = QLabel("-")
        self.modified_label.setObjectName("StatValue")
        info_layout.addWidget(self.modified_label, 5, 0, 1, 2)
        info_layout.setColumnStretch(0, 1)
        info_layout.setColumnStretch(1, 1)
        details_layout.addLayout(info_layout)
        
        folder_caption = QLabel("Fachordner")
        folder_caption.setObjectName("StatCaption")
        details_layout.addWidget(folder_caption)
        self.service_types_label = QLabel("-")
        self.service_types_label.setObjectName("StatValue")
        details_layout.addWidget(self.service_types_label)
        
        file_caption = QLabel("Dateien")
        file_caption.setObjectName("StatCaption")
        details_layout.addWidget(file_caption)
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(self.on_file_selected)
        details_layout.addWidget(self.file_list)

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
        
        main_widget.setLayout(main_layout)

    def apply_theme(self):
        app = QApplication.instance()
        if app is not None:
            self.theme_manager.apply(app)

    def open_settings_popup(self):
        if self.settings_popup is not None and self.settings_popup.isVisible():
            self.settings_popup.close()
            return

        self.settings_popup = SettingsPopup(
            self.theme_manager.mode,
            self.theme_manager.accent,
            self,
            data_path=self.index_source,
            indexing=self.index_worker is not None and self.index_worker.isRunning(),
        )
        self.settings_popup.appearanceChanged.connect(self.on_settings_appearance_changed)
        self.settings_popup.dataPathChanged.connect(self.on_settings_data_path_changed)
        self.settings_popup.reindexRequested.connect(self.on_settings_reindex_requested)
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
        if self.index_worker is not None and self.index_worker.isRunning():
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
            replace_existing=True,
            status_text=f"Baue Index für {new_source} neu auf …",
        )

    def on_settings_reindex_requested(self):
        if self.index_worker is not None and self.index_worker.isRunning():
            if self.settings_popup is not None:
                self.settings_popup.set_indexing(True)
            return
        if not self.index_source.exists() or not self.index_source.is_dir():
            if self.settings_popup is not None:
                self.settings_popup.set_indexing(False)
            QMessageBox.warning(self, "Indexierung", "Die konfigurierte Datenquelle existiert nicht.")
            return
        self._start_background_indexing(
            self.index_source,
            replace_existing=True,
            status_text=f"Baue Index für {self.index_source.name} neu auf …",
        )

    def _start_background_indexing(
        self,
        source: Path,
        replace_existing: bool,
        status_text: str,
    ):
        if self.index_worker is not None and self.index_worker.isRunning():
            return
        self.search_generation += 1
        self._cancel_outdated_searches()
        self.search_debounce.stop()
        self.search_input.setEnabled(False)
        self.search_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.status_label.setText(status_text)
        if self.settings_popup is not None:
            self.settings_popup.set_indexing(True)
        self.index_worker = IndexingWorker(DB_FILE, source, replace_existing=replace_existing)
        self.index_worker.finished.connect(self.on_indexing_complete)
        self.index_worker.start()
    
    def check_and_index(self):
        if not self.index_source.exists():
            self.status_label.setText(f"Indexquelle nicht gefunden: {self.index_source}")
            QMessageBox.warning(
                self,
                "Indexquelle fehlt",
                f"Der Datenordner wurde nicht gefunden:\n{self.index_source}\n\n"
                "Bitte den Ordner anlegen oder den Pfad in app/core/config.py anpassen."
            )
            return

        needs_index = (not DB_FILE.exists() or DB_FILE.stat().st_size == 0)
        if not needs_index:
            needs_index = not self.index_manager.has_index_for_root(self.index_source)
        if not needs_index:
            needs_index = self.index_manager.content_index_needs_rebuild(self.index_source)

        if needs_index:
            self._start_background_indexing(
                self.index_source,
                replace_existing=True,
                status_text="Indexiere Dateien und Dokumentinhalte …",
            )
        else:
            self.status_label.setText(f"Index geladen ✓ ({self.index_source.name})")
    
    def on_indexing_complete(self):
        worker = self.index_worker
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.search_input.setEnabled(True)
        self.search_button.setEnabled(True)
        if self.settings_popup is not None:
            self.settings_popup.set_indexing(False)
        if worker is not None and worker.error:
            self.status_label.setText(f"Indexierung fehlgeschlagen: {worker.error}")
            QMessageBox.warning(self, "Indexierung fehlgeschlagen", worker.error)
            self.pending_index_source = None
        else:
            if self.pending_index_source is not None:
                self.index_source = self.pending_index_source
                save_index_source(self.index_source)
                self.pending_index_source = None
            count = worker.indexed_count if worker is not None else 0
            self._reset_search_results()
            self.current_customer = None
            self.file_list.clear()
            self.status_label.setText(f"Index fertig geladen ✓ ({count} Dateien)")
        self.index_worker = None
    
    def _create_result_groups(self, parent_layout: QVBoxLayout):
        self.result_groups = {}
        self.result_group_titles = {}
        results_splitter = QSplitter(Qt.Vertical)
        results_splitter.setObjectName("ResultsSplitter")
        results_splitter.setChildrenCollapsible(False)

        for category, title in (
            ("folders", "Ordner"),
            ("files", "Dateinamen"),
            ("text", "Dokumentinhalte"),
        ):
            section = QWidget()
            section.setObjectName("ResultSection")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(5)

            heading = QLabel(title)
            heading.setObjectName("SearchGroupTitle")
            section_layout.addWidget(heading)

            result_list = QListWidget()
            result_list.setObjectName("ResultList")
            result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            result_list.itemClicked.connect(self.on_search_result_clicked)
            result_list.itemDoubleClicked.connect(self.on_search_result_double_clicked)
            section_layout.addWidget(result_list)

            results_splitter.addWidget(section)
            self.result_groups[category] = result_list
            self.result_group_titles[category] = (heading, title)

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
        self._refresh_result_group_titles()

    def on_search_text_changed(self, text: str):
        self.search_generation += 1
        self._cancel_outdated_searches()
        self.search_debounce.stop()
        query = text.strip()
        self.search_counts = {"folders": None, "files": None, "text": None}
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
        self.current_customer = None
        self.search_counts = {"folders": None, "files": None, "text": None}
        self._refresh_result_group_titles()

        for category in self.result_groups:
            self._replace_group_items(category, [self._placeholder_item("Suche läuft …")])

        self.status_label.setText("Durchsuche Ordner, Dateinamen und Dokumentinhalte parallel …")
        for category in ("folders", "files", "text"):
            self._launch_search(category, query, generation)

    def _launch_search(self, category: str, query: str, generation: int):
        worker = SearchWorker(DB_FILE, generation, category, query)
        worker.completed.connect(self._on_search_completed)
        worker.finished.connect(lambda worker=worker: self._release_search_worker(worker))
        self.search_workers.add(worker)
        worker.start()

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
        elif category == "folders":
            self._show_folder_results(results)
        elif category == "files":
            self._show_file_results(results)
        elif category == "text":
            self._show_text_results(results)
        self._update_search_status()

    def _show_folder_results(self, results):
        aggregated = {}
        for result in results:
            name = result.get("customer_name") or "Unbekannt"
            aggregated[name] = aggregated.get(name, 0) + int(result.get("file_count") or 0)

        items = []
        for name, file_count in sorted(aggregated.items(), key=lambda entry: entry[0].lower())[:100]:
            item = QListWidgetItem(f"{name}  ·  {file_count} Dateien")
            item.setData(self.RESULT_KIND_ROLE, "customer")
            item.setData(self.RESULT_VALUE_ROLE, name)
            items.append(item)
        if not items:
            items.append(self._placeholder_item("Keine Ordner oder Kunden gefunden"))
        self._replace_group_items("folders", items)
        self.search_counts["folders"] = len(aggregated)

    def _show_file_results(self, results):
        items = []
        for file_info in results[:100]:
            relative_dir = file_info.get("relative_dir") or ""
            suffix = f"  ·  {relative_dir}" if relative_dir else ""
            item = QListWidgetItem(f"{file_info['filename']}{suffix}")
            item.setData(self.RESULT_KIND_ROLE, "file")
            item.setData(self.RESULT_VALUE_ROLE, file_info["path"])
            item.setToolTip(file_info["path"])
            items.append(item)
        if not items:
            items.append(self._placeholder_item("Keine Dateinamen gefunden"))
        self._replace_group_items("files", items)
        self.search_counts["files"] = len(results)

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
            items.append(item)
        if not items:
            items.append(self._placeholder_item("Keine Texttreffer gefunden"))
        self._replace_group_items("text", items)
        self.search_counts["text"] = len(results)

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
            heading.setText(base_title if count is None else f"{base_title} ({count})")

    def on_search_result_clicked(self, item: QListWidgetItem):
        if item.data(self.RESULT_KIND_ROLE) == "customer":
            self.show_customer_details(item.data(self.RESULT_VALUE_ROLE))

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
        if self.index_worker is not None and self.index_worker.isRunning():
            self.index_worker.requestInterruption()
            self.index_worker.wait()
        self.index_manager.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
