from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QTextEdit, QTableWidget, QTableWidgetItem, QMessageBox, QSizePolicy
)
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtCore import Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import shutil
import subprocess
import openpyxl
import xlrd
from openpyxl.utils import get_column_letter


class FileViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("FileViewer")
        self.current_file = None
        self._conversion_dir = None
        self._pdf_document = None
        self._pdf_documents = []
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        header_layout = QHBoxLayout()
        self.file_info_label = QLabel("Keine Datei geladen")
        self.file_info_label.setObjectName("ViewerFileInfo")
        header_layout.addWidget(self.file_info_label)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        self.content_widget = QWidget()
        self.content_widget.setObjectName("ViewerContent")
        self.content_layout = QVBoxLayout()
        self.content_widget.setLayout(self.content_layout)
        
        scroll = QScrollArea()
        scroll.setObjectName("ViewerScrollArea")
        scroll.setWidget(self.content_widget)
        scroll.setWidgetResizable(True)
        
        layout.addWidget(scroll)
        self.setLayout(layout)
    
    def open_file(self, filepath: Path):
        if not filepath.exists():
            QMessageBox.warning(self, "Fehler", f"Datei nicht gefunden: {filepath}")
            return
        
        self.current_file = filepath
        self.file_info_label.setText(f"Datei: {filepath.name} ({filepath.stat().st_size / 1024:.2f} KB)")

        self._cleanup_conversion_dir()
        self._pdf_document = None
        self._clear_content()

        file_type = filepath.suffix.lower().lstrip('.')

        try:
            if file_type == 'pdf':
                self.show_pdf(filepath)
            elif file_type in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
                self.show_image(filepath)
            elif file_type == 'xlsx':
                self.show_excel(filepath)
            elif file_type == 'xls':
                self.show_legacy_excel(filepath)
            elif file_type in ['txt', 'csv', 'log', 'md']:
                self.show_text(filepath)
            elif file_type == 'docx':
                self.show_docx(filepath)
            elif file_type == 'doc':
                self.show_legacy_doc(filepath)
            else:
                self.show_generic(filepath)
        except Exception as e:
            error_label = QLabel(f"Fehler beim Öffnen: {str(e)}")
            error_label.setStyleSheet("color: red;")
            self.content_layout.addWidget(error_label)

    def _clear_content(self):
        while self.content_layout.count():
            layout_item = self.content_layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                if isinstance(widget, QPdfView):
                    document = widget.document()
                    if document is not None:
                        document.close()
                        self._release_pdf_document(document)
                widget.deleteLater()
    
    def show_image(self, filepath: Path):
        try:
            pixmap = QPixmap(str(filepath))
            
            if pixmap.isNull():
                raise ValueError("Bild konnte nicht geladen werden")
            
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
        try:
            pdf_view = QPdfView()
            pdf_view.setObjectName("PdfView")
            pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            pdf_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            pdf_view.setMinimumHeight(500)

            document = QPdfDocument(pdf_view)
            error = document.load(str(filepath))
            if error != QPdfDocument.Error.None_:
                pdf_view.deleteLater()
                self.show_error(f"PDF konnte nicht geladen werden ({error.name}).")
                return

            pdf_view.setDocument(document)
            self._pdf_document = document
            self._pdf_documents.append(document)
            pdf_view.destroyed.connect(
                lambda _=None, document=document: self._release_pdf_document(document)
            )

            page_label = QLabel(f"{document.pageCount()} Seiten · vollständig scrollbar")
            page_label.setObjectName("ViewerMeta")
            self.content_layout.addWidget(page_label)
            self.content_layout.addWidget(pdf_view, 1)
        except Exception as e:
            self.show_error(f"Fehler beim Anzeigen der PDF: {e}")

    def _convert_with_libreoffice(self, filepath: Path, target_extension: str) -> Path:
        executable = shutil.which("libreoffice") or shutil.which("soffice")
        if executable is None:
            raise RuntimeError("LibreOffice wurde nicht gefunden")

        conversion_dir = TemporaryDirectory(prefix="papagui-viewer-")
        output_dir = Path(conversion_dir.name)
        profile_dir = output_dir / "libreoffice-profile"
        profile_dir.mkdir()
        runtime_dir = output_dir / "runtime"
        runtime_dir.mkdir(mode=0o700)
        config_dir = output_dir / "config"
        config_dir.mkdir()
        cache_dir = output_dir / "cache"
        cache_dir.mkdir()
        command = [
            executable,
            "--headless",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            target_extension,
            "--outdir",
            str(output_dir),
            str(filepath),
        ]
        environment = os.environ.copy()
        environment.update({
            "HOME": str(output_dir),
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "XDG_CONFIG_HOME": str(config_dir),
            "XDG_CACHE_HOME": str(cache_dir),
            "SAL_USE_VCLPLUGIN": "svp",
        })
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=45,
                env=environment,
            )
        except Exception:
            conversion_dir.cleanup()
            raise

        candidates = [
            path for path in output_dir.iterdir()
            if path.is_file() and path.suffix.lower() == f".{target_extension.lower()}"
        ]
        if result.returncode != 0 or not candidates:
            details = (result.stderr or result.stdout).strip()
            message = details or f"LibreOffice-Fehlercode {result.returncode}"
            conversion_dir.cleanup()
            raise RuntimeError(message)

        self._conversion_dir = conversion_dir
        return candidates[0]

    def _release_pdf_document(self, document: QPdfDocument):
        if document in self._pdf_documents:
            self._pdf_documents.remove(document)

    def _cleanup_conversion_dir(self):
        if self._conversion_dir is not None:
            self._conversion_dir.cleanup()
            self._conversion_dir = None

    def show_legacy_excel(self, filepath: Path):
        try:
            workbook = xlrd.open_workbook(str(filepath), on_demand=True)
            worksheet = workbook.sheet_by_index(0)
            max_rows = min(50, worksheet.nrows)
            max_cols = min(15, worksheet.ncols)

            table = QTableWidget(max_rows, max_cols)
            for row_idx in range(max_rows):
                for col_idx in range(max_cols):
                    value = worksheet.cell_value(row_idx, col_idx)
                    if isinstance(value, float) and value.is_integer():
                        value = int(value)
                    table_item = QTableWidgetItem(str(value) if value != "" else "")
                    table_item.setFlags(table_item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row_idx, col_idx, table_item)

            table.setHorizontalHeaderLabels(
                [get_column_letter(index) for index in range(1, max_cols + 1)]
            )
            table.resizeColumnsToContents()
            self.content_layout.addWidget(table)

            info_label = QLabel(
                f"Zeilen: {worksheet.nrows}, Spalten: {worksheet.ncols} "
                f"(angezeigt: {max_rows}x{max_cols})"
            )
            info_label.setObjectName("ViewerMeta")
            self.content_layout.addWidget(info_label)
            workbook.release_resources()
        except Exception as direct_error:
            try:
                converted = self._convert_with_libreoffice(filepath, "xlsx")
                self.show_excel(converted)
            except Exception as conversion_error:
                self.show_error(
                    "Alte Excel-Datei konnte nicht gelesen werden: "
                    f"{direct_error}; Konvertierung: {conversion_error}"
                )

    def show_legacy_doc(self, filepath: Path):
        catdoc = shutil.which("catdoc")
        try:
            if catdoc is None:
                raise RuntimeError("catdoc wurde nicht gefunden")
            result = subprocess.run(
                [catdoc, str(filepath)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=20,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "catdoc ist fehlgeschlagen")

            info_label = QLabel("Textansicht aus altem Word-Dokument (.doc)")
            info_label.setObjectName("ViewerMeta")
            self.content_layout.addWidget(info_label)
            text_widget = QTextEdit()
            text_widget.setReadOnly(True)
            text_widget.setPlainText(result.stdout)
            self.content_layout.addWidget(text_widget)
            return
        except Exception as text_error:
            try:
                converted = self._convert_with_libreoffice(filepath, "pdf")
                self.show_pdf(converted)
            except Exception as conversion_error:
                self.show_error(
                    "Alte Word-Datei konnte nicht gelesen werden: "
                    f"{text_error}; Konvertierung: {conversion_error}"
                )
    
    def show_excel(self, filepath: Path):
        try:
            wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
            ws = wb.active
            
            table = QTableWidget()
            
            max_rows = min(50, ws.max_row)
            max_cols = min(15, ws.max_column)
            
            table.setRowCount(max_rows)
            table.setColumnCount(max_cols)
            
            for row_idx in range(1, max_rows + 1):
                for col_idx in range(1, max_cols + 1):
                    cell = ws.cell(row_idx, col_idx)
                    value = str(cell.value) if cell.value is not None else ""
                    
                    table_item = QTableWidgetItem(value)
                    table_item.setFlags(table_item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row_idx - 1, col_idx - 1, table_item)
            
            headers = [get_column_letter(i) for i in range(1, max_cols + 1)]
            table.setHorizontalHeaderLabels(headers)
            
            table.resizeColumnsToContents()
            self.content_layout.addWidget(table)
            
            info_label = QLabel(f"Zeilen: {ws.max_row}, Spalten: {ws.max_column} (angezeigt: {max_rows}x{max_cols})")
            self.content_layout.addWidget(info_label)
            
            wb.close()
        except Exception as e:
            self.show_error(f"Fehler beim Lesen der Excel-Datei: {e}")
    
    def show_text(self, filepath: Path):
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            text_widget = QTextEdit()
            text_widget.setReadOnly(True)
            text_widget.setPlainText(content[:50000])
            text_widget.setFont(QFont("Courier", 10))
            
            self.content_layout.addWidget(text_widget)
            
            if len(content) > 50000:
                info_label = QLabel("⚠ Datei gekürzt (erste 50KB angezeigt)")
                info_label.setStyleSheet("color: orange;")
                self.content_layout.addWidget(info_label)
        except Exception as e:
            self.show_error(f"Fehler beim Lesen der Textdatei: {e}")
    
    def show_docx(self, filepath: Path):
        try:
            from docx import Document
            doc = Document(str(filepath))
            
            text_widget = QTextEdit()
            text_widget.setReadOnly(True)
            
            full_text = ""
            for para in doc.paragraphs[:100]:
                if para.text:
                    full_text += para.text + "\n"
            
            text_widget.setPlainText(full_text)
            self.content_layout.addWidget(text_widget)
        except ImportError:
            self.show_error("python-docx nicht installiert. Bitte installieren: pip install python-docx")
        except Exception as e:
            self.show_error(f"Fehler beim Lesen des DOCX: {e}")
    
    def show_generic(self, filepath: Path):
        label = QLabel(f"📁 Dateiformat: {filepath.suffix}")
        label.setFont(QFont("Arial", 12))
        
        info_text = f"""Dateiname: {filepath.name}
Pfad: {filepath}
Größe: {filepath.stat().st_size / 1024:.2f} KB

Dieses Dateiformat wird noch nicht unterstützt."""
        
        info_label = QLabel(info_text)
        self.content_layout.addWidget(label)
        self.content_layout.addWidget(info_label)
    
    def show_error(self, message: str):
        error_label = QLabel(f"❌ {message}")
        error_label.setStyleSheet("color: red; font-weight: bold;")
        self.content_layout.addWidget(error_label)

    def closeEvent(self, event):
        self._clear_content()
        self._cleanup_conversion_dir()
        super().closeEvent(event)
