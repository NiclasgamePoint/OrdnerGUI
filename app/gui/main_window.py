import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget, QLabel, QSplitter,
    QMessageBox, QProgressBar, QToolButton
)
from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QIcon
from datetime import datetime

from app.core.config import WINDOW_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT, DB_FILE, get_default_index_source
from app.core.index_manager import IndexManager
from app.gui.viewer import FileViewer
from app.gui.theme import ThemeManager
from app.gui.settings_popup import SettingsPopup


class IndexingWorker(QThread):
    progress = Signal(int)
    finished = Signal()
    
    def __init__(self, manager: IndexManager, base_path: Path):
        super().__init__()
        self.manager = manager
        self.base_path = base_path
    
    def run(self):
        self.manager.index_directory(self.base_path)
        self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setGeometry(100, 100, WINDOW_WIDTH, WINDOW_HEIGHT)
        
        self.index_manager = IndexManager(DB_FILE)
        self.current_customer = None
        self.index_worker = None
        self.index_source = get_default_index_source()
        self.theme_manager = ThemeManager()
        self.settings_popup = None
        
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

        top_bar = QWidget()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(48)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 4, 10, 4)
        top_layout.setSpacing(8)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(0)
        page_title = QLabel("PapaGUI")
        page_title.setObjectName("PageTitle")
        title_layout.addWidget(page_title)
        top_layout.addLayout(title_layout)
        top_layout.addStretch()

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
        top_layout.addWidget(self.settings_button)

        main_layout.addWidget(top_bar)

        search_card = QWidget()
        search_card.setObjectName("SearchCard")
        search_card.setFixedHeight(50)
        search_layout = QHBoxLayout(search_card)
        search_layout.setContentsMargins(12, 7, 12, 7)
        search_layout.setSpacing(8)
        
        search_label = QLabel("Vorgang-/Kundensuche:")
        search_label.setObjectName("StatCaption")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Kunde, Projekt, Ordner oder Dateiname eingeben...")
        self.search_input.textChanged.connect(self.on_search_changed)
        
        self.search_file_input = QLineEdit()
        self.search_file_input.setPlaceholderText("Dateiname suchen...")
        self.search_file_input.textChanged.connect(self.on_file_search_changed)
        
        self.search_text_input = QLineEdit()
        self.search_text_input.setPlaceholderText("Text in Dateien suchen (ripgrep)...")
        search_text_btn = QPushButton("Suchen")
        search_text_btn.clicked.connect(self.on_text_search)
        
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(QLabel("Dateiname:"))
        search_layout.addWidget(self.search_file_input)
        search_layout.addWidget(QLabel("Text:"))
        search_layout.addWidget(self.search_text_input)
        search_layout.addWidget(search_text_btn)
        
        main_layout.addWidget(search_card)
        
        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        
        left_card = QWidget()
        left_card.setObjectName("SideCard")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_title = QLabel("Vorgänge/Kunden")
        left_title.setObjectName("StatValue")
        left_layout.addWidget(left_title)
        self.customer_list = QListWidget()
        self.customer_list.itemClicked.connect(self.on_customer_selected)
        left_layout.addWidget(self.customer_list)

        details_card = QWidget()
        details_card.setObjectName("DetailsCard")
        details_outer_layout = QVBoxLayout(details_card)
        details_outer_layout.setContentsMargins(12, 12, 12, 12)
        details_outer_layout.setSpacing(10)

        right_widget = QTabWidget()
        
        details_layout = QVBoxLayout()
        details_layout.setContentsMargins(8, 8, 8, 8)
        details_layout.setSpacing(10)
        
        info_layout = QHBoxLayout()
        info_layout.setSpacing(14)

        name_caption = QLabel("Vorgang/Kunde")
        name_caption.setObjectName("StatCaption")
        info_layout.addWidget(name_caption)
        self.customer_name_label = QLabel("-")
        self.customer_name_label.setObjectName("StatValue")
        info_layout.addWidget(self.customer_name_label)
        
        files_caption = QLabel("Dateien")
        files_caption.setObjectName("StatCaption")
        info_layout.addWidget(files_caption)
        self.file_count_label = QLabel("0")
        self.file_count_label.setObjectName("StatValue")
        info_layout.addWidget(self.file_count_label)
        
        size_caption = QLabel("Größe")
        size_caption.setObjectName("StatCaption")
        info_layout.addWidget(size_caption)
        self.size_label = QLabel("0 B")
        self.size_label.setObjectName("StatValue")
        info_layout.addWidget(self.size_label)
        
        modified_caption = QLabel("Zuletzt geändert")
        modified_caption.setObjectName("StatCaption")
        info_layout.addWidget(modified_caption)
        self.modified_label = QLabel("-")
        self.modified_label.setObjectName("StatValue")
        info_layout.addWidget(self.modified_label)
        
        info_layout.addStretch()
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
        
        details_widget = QWidget()
        details_widget.setLayout(details_layout)
        
        self.file_viewer = FileViewer()
        
        right_widget.addTab(details_widget, "Kundendetails")
        right_widget.addTab(self.file_viewer, "Datei-Viewer")

        details_outer_layout.addWidget(right_widget)
        
        content_splitter.addWidget(left_card)
        content_splitter.addWidget(details_card)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 3)
        
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

        self.settings_popup = SettingsPopup(self.theme_manager.mode, self.theme_manager.accent, self)
        self.settings_popup.appearanceChanged.connect(self.on_settings_appearance_changed)
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

        if needs_index:
            self.status_label.setText("Indexiere Dateien...")
            self.progress_bar.setVisible(True)
            
            self.index_worker = IndexingWorker(self.index_manager, self.index_source)
            self.index_worker.finished.connect(self.on_indexing_complete)
            self.index_worker.start()
        else:
            self.status_label.setText(f"Index geladen ✓ ({self.index_source.name})")
    
    def on_indexing_complete(self):
        self.progress_bar.setVisible(False)
        self.status_label.setText("Index fertig geladen ✓")
        self.index_worker = None
    
    def on_search_changed(self, text: str):
        self.customer_list.clear()
        
        if len(text) < 1:
            return
        
        results = self.index_manager.search_customers(text)
        
        unique_customers = set()
        for result in results:
            unique_customers.add(result['customer_name'])
        
        for customer in sorted(unique_customers):
            item = QListWidgetItem(customer)
            self.customer_list.addItem(item)
        
        self.status_label.setText(f"✓ {len(unique_customers)} Kunden gefunden")
    
    def on_customer_selected(self, item: QListWidgetItem):
        customer_name = item.text()
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
            bucket = file_info.get('time_bucket') or file_info.get('year') or '-'
            folder = file_info.get('domain_folder') or file_info.get('service_type') or '-'
            subfolder = file_info.get('subfolder') or '-'
            item_text = f"{folder}/{bucket}/{subfolder}/{file_info['filename']}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, file_info['path'])
            self.file_list.addItem(item)
        
        self.status_label.setText(f"✓ Kunde: {customer_name} mit {details['file_count']} Dateien")
    
    def on_file_search_changed(self, text: str):
        if len(text) < 2:
            return
        
        results = self.index_manager.search_files(text, self.current_customer)
        
        self.file_list.clear()
        for file_info in results:
            item_text = f"{file_info['filename']} ({file_info['file_type']})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, file_info['path'])
            self.file_list.addItem(item)
        
        self.status_label.setText(f"✓ {len(results)} Dateien gefunden")
    
    def on_text_search(self):
        query = self.search_text_input.text()
        if len(query) < 2:
            QMessageBox.warning(self, "Suche", "Suchtext muss mindestens 2 Zeichen lang sein")
            return
        
        self.status_label.setText("Suche in Textdateien...")
        results = self.index_manager.search_in_text(query, self.current_customer)
        
        self.file_list.clear()
        for filepath, line_num in results[:50]:
            item_text = f"{filepath.name} (Zeile {line_num})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, str(filepath))
            self.file_list.addItem(item)
        
        self.status_label.setText(f"✓ {len(results)} Treffer gefunden")
    
    def on_file_selected(self, item: QListWidgetItem):
        filepath = item.data(Qt.UserRole)
        if filepath:
            self.file_viewer.open_file(Path(filepath))
    
    def closeEvent(self, event):
        self.index_manager.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
