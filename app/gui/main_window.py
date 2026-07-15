import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget, QLabel, QSplitter, QTextEdit,
    QFileDialog, QMessageBox, QProgressBar, QFileIconProvider
)
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap
import threading
from datetime import datetime

from app.core.config import WINDOW_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT, DB_FILE, get_default_index_source
from app.core.index_manager import IndexManager
from app.gui.viewer import FileViewer


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
        
        self.init_ui()
        self.check_and_index()
    
    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout()
        
        search_layout = QHBoxLayout()
        
        search_label = QLabel("Vorgang-/Kundensuche:")
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
        
        main_layout.addLayout(search_layout)
        
        content_splitter = QSplitter(Qt.Horizontal)
        
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Vorgänge/Kunden:"))
        self.customer_list = QListWidget()
        self.customer_list.itemClicked.connect(self.on_customer_selected)
        left_layout.addWidget(self.customer_list)
        
        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        
        right_widget = QTabWidget()
        
        details_layout = QVBoxLayout()
        
        info_layout = QHBoxLayout()
        info_layout.addWidget(QLabel("Vorgang/Kunde:"))
        self.customer_name_label = QLabel("-")
        self.customer_name_label.setStyleSheet("font-weight: bold;")
        info_layout.addWidget(self.customer_name_label)
        
        info_layout.addWidget(QLabel("Dateien:"))
        self.file_count_label = QLabel("0")
        info_layout.addWidget(self.file_count_label)
        
        info_layout.addWidget(QLabel("Größe:"))
        self.size_label = QLabel("0 B")
        info_layout.addWidget(self.size_label)
        
        info_layout.addWidget(QLabel("Zuletzt geändert:"))
        self.modified_label = QLabel("-")
        info_layout.addWidget(self.modified_label)
        
        info_layout.addStretch()
        details_layout.addLayout(info_layout)
        
        details_layout.addWidget(QLabel("Fachordner:"))
        self.service_types_label = QLabel("-")
        details_layout.addWidget(self.service_types_label)
        
        details_layout.addWidget(QLabel("Dateien:"))
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(self.on_file_selected)
        details_layout.addWidget(self.file_list)
        
        details_widget = QWidget()
        details_widget.setLayout(details_layout)
        
        self.file_viewer = FileViewer()
        
        right_widget.addTab(details_widget, "Kundendetails")
        right_widget.addTab(self.file_viewer, "Datei-Viewer")
        
        content_splitter.addWidget(left_widget)
        content_splitter.addWidget(right_widget)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 2)
        
        main_layout.addWidget(content_splitter)
        
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Bereit...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        
        main_layout.addLayout(status_layout)
        
        main_widget.setLayout(main_layout)
    
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
