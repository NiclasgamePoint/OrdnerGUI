"""Datei-Viewer für verschiedene Dateitypen"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QTextEdit, QTableWidget, QTableWidgetItem, QMessageBox
)
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtCore import Qt
from pathlib import Path
import openpyxl
from openpyxl.utils import get_column_letter


class FileViewer(QWidget):
    """Viewer für verschiedene Dateitypen"""
    
    def __init__(self):
        super().__init__()
        self.current_file = None
        self.init_ui()
    
    def init_ui(self):
        """Initialisiert die UI"""
        layout = QVBoxLayout()
        
        # Header mit Dateiinfo
        header_layout = QHBoxLayout()
        self.file_info_label = QLabel("Keine Datei geladen")
        self.file_info_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        header_layout.addWidget(self.file_info_label)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        # Content Area
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_widget.setLayout(self.content_layout)
        
        scroll = QScrollArea()
        scroll.setWidget(self.content_widget)
        scroll.setWidgetResizable(True)
        
        layout.addWidget(scroll)
        self.setLayout(layout)
    
    def open_file(self, filepath: Path):
        """Öffnet eine Datei"""
        if not filepath.exists():
            QMessageBox.warning(self, "Fehler", f"Datei nicht gefunden: {filepath}")
            return
        
        self.current_file = filepath
        self.file_info_label.setText(f"Datei: {filepath.name} ({filepath.stat().st_size / 1024:.2f} KB)")
        
        # Bisheriges Content löschen
        while self.content_layout.count():
            self.content_layout.takeAt(0).widget().deleteLater()
        
        file_type = filepath.suffix.lower().lstrip('.')
        
        try:
            if file_type == 'pdf':
                self.show_pdf(filepath)
            elif file_type in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
                self.show_image(filepath)
            elif file_type in ['xlsx', 'xls']:
                self.show_excel(filepath)
            elif file_type in ['txt', 'csv', 'log', 'md']:
                self.show_text(filepath)
            elif file_type == 'docx':
                self.show_docx(filepath)
            else:
                self.show_generic(filepath)
        except Exception as e:
            error_label = QLabel(f"Fehler beim Öffnen: {str(e)}")
            error_label.setStyleSheet("color: red;")
            self.content_layout.addWidget(error_label)
    
    def show_image(self, filepath: Path):
        """Zeigt ein Bild an"""
        try:
            pixmap = QPixmap(str(filepath))
            
            if pixmap.isNull():
                raise ValueError("Bild konnte nicht geladen werden")
            
            # Größe begrenzen
            max_width = 800
            max_height = 600
            if pixmap.width() > max_width or pixmap.height() > max_height:
                pixmap = pixmap.scaledToWidth(max_width, Qt.SmoothTransformation)
            
            image_label = QLabel()
            image_label.setPixmap(pixmap)
            image_label.setAlignment(Qt.AlignCenter)
            
            self.content_layout.addWidget(image_label)
        except Exception as e:
            self.show_error(f"Fehler beim Laden des Bildes: {e}")
    
    def show_pdf(self, filepath: Path):
        """Zeigt PDF-Info (vollständiger PDF-Viewer würde PyPDF2 oder fitz benötigen)"""
        try:
            # Einfache Info-Anzeige
            label = QLabel("📄 PDF-Dokument")
            label.setFont(QFont("Arial", 14))
            self.content_layout.addWidget(label)
            
            info_text = f"""
            Datei: {filepath.name}
            Größe: {filepath.stat().st_size / 1024:.2f} KB
            
            Hinweis: Für vollständige PDF-Anzeige würde PyPDF2/fitz benötigt.
            Momentan wird nur die Datei-Info gezeigt.
            """
            
            info_label = QLabel(info_text)
            self.content_layout.addWidget(info_label)
            
            # Versuche PDF-Text zu extrahieren (vereinfacht)
            try:
                import PyPDF2
                with open(filepath, 'rb') as f:
                    pdf = PyPDF2.PdfReader(f)
                    if len(pdf.pages) > 0:
                        first_page_text = pdf.pages[0].extract_text()
                        text_widget = QTextEdit()
                        text_widget.setReadOnly(True)
                        text_widget.setPlainText(f"Seite 1 Preview:\n\n{first_page_text[:500]}...")
                        self.content_layout.addWidget(text_widget)
            except ImportError:
                pass
        except Exception as e:
            self.show_error(f"Fehler beim Lesen der PDF: {e}")
    
    def show_excel(self, filepath: Path):
        """Zeigt Excel-Datei an"""
        try:
            wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
            ws = wb.active
            
            # Tabelle erstellen
            table = QTableWidget()
            
            # Größe ermitteln
            max_rows = min(50, ws.max_row)  # Limit auf 50 Zeilen
            max_cols = min(15, ws.max_column)  # Limit auf 15 Spalten
            
            table.setRowCount(max_rows)
            table.setColumnCount(max_cols)
            
            # Daten ausfüllen
            for row_idx in range(1, max_rows + 1):
                for col_idx in range(1, max_cols + 1):
                    cell = ws.cell(row_idx, col_idx)
                    value = str(cell.value) if cell.value is not None else ""
                    
                    table_item = QTableWidgetItem(value)
                    table_item.setFlags(table_item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row_idx - 1, col_idx - 1, table_item)
            
            # Spaltennamen
            headers = [get_column_letter(i) for i in range(1, max_cols + 1)]
            table.setHorizontalHeaderLabels(headers)
            
            table.resizeColumnsToContents()
            self.content_layout.addWidget(table)
            
            # Info
            info_label = QLabel(f"Zeilen: {ws.max_row}, Spalten: {ws.max_column} (angezeigt: {max_rows}x{max_cols})")
            self.content_layout.addWidget(info_label)
            
            wb.close()
        except Exception as e:
            self.show_error(f"Fehler beim Lesen der Excel-Datei: {e}")
    
    def show_text(self, filepath: Path):
        """Zeigt Textdatei an"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            text_widget = QTextEdit()
            text_widget.setReadOnly(True)
            text_widget.setPlainText(content[:50000])  # Limit auf 50KB
            text_widget.setFont(QFont("Courier", 10))
            
            self.content_layout.addWidget(text_widget)
            
            if len(content) > 50000:
                info_label = QLabel("⚠ Datei gekürzt (erste 50KB angezeigt)")
                info_label.setStyleSheet("color: orange;")
                self.content_layout.addWidget(info_label)
        except Exception as e:
            self.show_error(f"Fehler beim Lesen der Textdatei: {e}")
    
    def show_docx(self, filepath: Path):
        """Zeigt Word-Dokument an"""
        try:
            from docx import Document
            doc = Document(str(filepath))
            
            text_widget = QTextEdit()
            text_widget.setReadOnly(True)
            
            # Extrahiere Text
            full_text = ""
            for para in doc.paragraphs[:100]:  # Limit auf 100 Absätze
                if para.text:
                    full_text += para.text + "\n"
            
            text_widget.setPlainText(full_text)
            self.content_layout.addWidget(text_widget)
        except ImportError:
            self.show_error("python-docx nicht installiert. Bitte installieren: pip install python-docx")
        except Exception as e:
            self.show_error(f"Fehler beim Lesen des DOCX: {e}")
    
    def show_generic(self, filepath: Path):
        """Zeigt generische Datei-Info"""
        label = QLabel(f"📁 Dateiformat: {filepath.suffix}")
        label.setFont(QFont("Arial", 12))
        
        info_text = f"""
        Dateiname: {filepath.name}
        Pfad: {filepath}
        Größe: {filepath.stat().st_size / 1024:.2f} KB
        
        Dieses Dateiformat wird noch nicht unterstützt.
        """
        
        info_label = QLabel(info_text)
        self.content_layout.addWidget(label)
        self.content_layout.addWidget(info_label)
    
    def show_error(self, message: str):
        """Zeigt Fehlermeldung an"""
        error_label = QLabel(f"❌ {message}")
        error_label.setStyleSheet("color: red; font-weight: bold;")
        self.content_layout.addWidget(error_label)
