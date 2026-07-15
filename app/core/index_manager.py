import sqlite3
from pathlib import Path
from datetime import datetime
import os
from typing import Callable, List, Dict, Optional
import subprocess
import shutil
import time
import re
import sys
import json
from tempfile import TemporaryDirectory

import openpyxl
import xlrd
from docx import Document
from PyPDF2 import PdfReader


class IndexManager:
    SEARCH_LABEL_SQL = "COALESCE(NULLIF(project_name, ''), NULLIF(customer_name, ''), filename)"
    CONTENT_INDEX_TYPES = {"pdf", "doc", "docx", "xls", "xlsx"}
    MAX_EXTRACTED_CHARACTERS = 2_000_000

    def __init__(self, db_path: Path, initialize: bool = True):
        self.db_path = db_path
        self.conn = None
        if initialize:
            self.init_db()
        else:
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
    
    def init_db(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER,
                file_type TEXT,
                created_date TIMESTAMP,
                modified_date TIMESTAMP,
                year TEXT,
                service_type TEXT,
                customer_name TEXT,
                subfolder TEXT,
                full_text_indexed BOOLEAN DEFAULT 0,
                domain_folder TEXT,
                time_bucket TEXT,
                project_name TEXT,
                relative_dir TEXT,
                index_root TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indexed_roots (
                root_path TEXT PRIMARY KEY,
                last_indexed TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS file_content_fts USING fts5(
                path UNINDEXED,
                content,
                tokenize = 'unicode61 remove_diacritics 2'
            )
        """)

        self._ensure_column(cursor, "files", "domain_folder", "TEXT")
        self._ensure_column(cursor, "files", "time_bucket", "TEXT")
        self._ensure_column(cursor, "files", "project_name", "TEXT")
        self._ensure_column(cursor, "files", "relative_dir", "TEXT")
        self._ensure_column(cursor, "files", "index_root", "TEXT")
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer ON files(customer_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_service_type ON files(service_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_year ON files(year)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_filename ON files(filename)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_domain_folder ON files(domain_folder)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_time_bucket ON files(time_bucket)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_name ON files(project_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_index_root ON files(index_root)
        """)
        
        self.conn.commit()

    def _ensure_column(self, cursor: sqlite3.Cursor, table: str, column: str, col_type: str):
        cursor.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cursor.fetchall()}
        if column not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def _is_year_bucket(self, value: Optional[str]) -> bool:
        if not value:
            return False
        return len(value) == 4 and value.isdigit()

    def _derive_project_from_filename(self, filename: str) -> str:
        stem = Path(filename).stem.strip()
        separators = [" vom ", " - ", ","]
        for sep in separators:
            if sep in stem:
                left = stem.split(sep, 1)[0].strip()
                if left:
                    return left
        return stem

    def has_index_for_root(self, base_path: Path) -> bool:
        cursor = self.conn.cursor()
        root = str(base_path)
        cursor.execute("SELECT 1 FROM indexed_roots WHERE root_path = ? LIMIT 1", (root,))
        return cursor.fetchone() is not None

    def content_index_needs_rebuild(self, base_path: Path) -> bool:
        cursor = self.conn.cursor()
        placeholders = ", ".join("?" for _ in self.CONTENT_INDEX_TYPES)
        cursor.execute(
            f"""
            SELECT 1 FROM files
            WHERE index_root = ?
              AND file_type IN ({placeholders})
              AND COALESCE(full_text_indexed, 0) = 0
            LIMIT 1
            """,
            (str(base_path), *sorted(self.CONTENT_INDEX_TYPES)),
        )
        return cursor.fetchone() is not None
    
    def index_directory(
        self,
        base_path: Path,
        replace_existing: bool = False,
        should_cancel: Optional[Callable[[], bool]] = None,
    ):
        if not base_path.exists():
            raise FileNotFoundError(f"Index-Pfad existiert nicht: {base_path}")

        cursor = self.conn.cursor()
        indexed_count = 0
        root = str(base_path)

        if replace_existing:
            cursor.execute("DELETE FROM file_content_fts")
            cursor.execute("DELETE FROM files")
            cursor.execute("DELETE FROM indexed_roots")
        else:
            cursor.execute(
                "DELETE FROM file_content_fts WHERE path IN "
                "(SELECT path FROM files WHERE index_root = ? OR path LIKE ?)",
                (root, f"{root}{os.sep}%"),
            )
            cursor.execute("DELETE FROM files WHERE index_root = ?", (root,))
            cursor.execute("DELETE FROM files WHERE path LIKE ?", (f"{root}{os.sep}%",))
        
        for filepath in base_path.rglob('*'):
            if should_cancel is not None and should_cancel():
                self.conn.rollback()
                raise InterruptedError("Indexierung wurde abgebrochen")
            if filepath.is_file():
                try:
                    self._index_file(filepath, base_path, cursor)
                    indexed_count += 1
                except Exception as e:
                    print(f"Fehler beim Indexieren von {filepath}: {e}")

        cursor.execute(
            "INSERT OR REPLACE INTO indexed_roots (root_path, last_indexed) VALUES (?, ?)",
            (root, datetime.now().isoformat())
        )
        
        self.conn.commit()
        print(f"✓ {indexed_count} Dateien indiziert")
        return indexed_count
    
    def _index_file(self, filepath: Path, base_path: Path, cursor: sqlite3.Cursor):
        try:
            stat = filepath.stat()
            rel_path = str(filepath.relative_to(base_path))
            parts = rel_path.split(os.sep)
            
            domain_folder = parts[0] if len(parts) > 0 else None
            time_bucket = parts[1] if len(parts) > 1 else None

            project_name = None
            subfolder = None
            if len(parts) >= 4:
                project_name = parts[2]
            elif len(parts) == 3:
                project_name = self._derive_project_from_filename(filepath.name)

            if len(parts) >= 5:
                subfolder = parts[3]

            year = time_bucket if self._is_year_bucket(time_bucket) else None
            service_type = domain_folder
            customer_name = project_name or self._derive_project_from_filename(filepath.name)
            
            file_type = filepath.suffix.lower().lstrip('.')
            relative_dir = str(filepath.parent.relative_to(base_path))
            
            cursor.execute("""
                INSERT OR REPLACE INTO files 
                (path, filename, file_size, file_type, created_date, modified_date, 
                 year, service_type, customer_name, subfolder,
                 domain_folder, time_bucket, project_name, relative_dir, index_root,
                 full_text_indexed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(filepath),
                filepath.name,
                stat.st_size,
                file_type,
                datetime.fromtimestamp(stat.st_ctime),
                datetime.fromtimestamp(stat.st_mtime),
                year,
                service_type,
                customer_name,
                subfolder,
                domain_folder,
                time_bucket,
                project_name,
                relative_dir,
                str(base_path),
                0,
            ))

            if file_type in self.CONTENT_INDEX_TYPES:
                cursor.execute("DELETE FROM file_content_fts WHERE path = ?", (str(filepath),))
                content = self._extract_document_text(filepath, file_type)
                if content:
                    cursor.execute(
                        "INSERT INTO file_content_fts (path, content) VALUES (?, ?)",
                        (str(filepath), content),
                    )
                cursor.execute(
                    "UPDATE files SET full_text_indexed = 1 WHERE path = ?",
                    (str(filepath),),
                )
        except Exception as e:
            print(f"Fehler beim Indexieren von {filepath}: {e}")

    def _limit_text(self, text: str) -> str:
        return text[:self.MAX_EXTRACTED_CHARACTERS]

    def _extract_document_text(self, filepath: Path, file_type: str) -> str:
        try:
            if file_type == "pdf":
                return self._extract_pdf_text(filepath)
            if file_type == "docx":
                return self._extract_docx_text(filepath)
            if file_type == "doc":
                return self._extract_doc_text(filepath)
            if file_type == "xlsx":
                return self._extract_xlsx_text(filepath)
            if file_type == "xls":
                return self._extract_xls_text(filepath)
        except Exception as exc:
            print(f"Textextraktion fehlgeschlagen ({filepath}): {exc}")
        return ""

    def _extract_pdf_text(self, filepath: Path) -> str:
        reader = PdfReader(str(filepath))
        parts = []
        length = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            if text:
                parts.append(text)
                length += len(text)
            if length >= self.MAX_EXTRACTED_CHARACTERS:
                break
        return self._limit_text("\n".join(parts))

    def _extract_docx_text(self, filepath: Path) -> str:
        document = Document(str(filepath))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
        length = sum(map(len, parts))
        for table in document.tables:
            for row in table.rows:
                values = [cell.text for cell in row.cells if cell.text]
                if values:
                    line = "\t".join(values)
                    parts.append(line)
                    length += len(line)
                if length >= self.MAX_EXTRACTED_CHARACTERS:
                    return self._limit_text("\n".join(parts))
        return self._limit_text("\n".join(parts))

    def _extract_xlsx_text(self, filepath: Path) -> str:
        workbook = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
        try:
            parts = []
            length = 0
            for worksheet in workbook.worksheets:
                parts.append(worksheet.title)
                for row in worksheet.iter_rows(values_only=True):
                    values = [str(value) for value in row if value is not None]
                    if values:
                        line = "\t".join(values)
                        parts.append(line)
                        length += len(line)
                    if length >= self.MAX_EXTRACTED_CHARACTERS:
                        return self._limit_text("\n".join(parts))
            return self._limit_text("\n".join(parts))
        finally:
            workbook.close()

    def _extract_xls_text(self, filepath: Path) -> str:
        workbook = xlrd.open_workbook(str(filepath), on_demand=True)
        try:
            parts = []
            length = 0
            for worksheet in workbook.sheets():
                parts.append(worksheet.name)
                for row_index in range(worksheet.nrows):
                    values = [
                        str(worksheet.cell_value(row_index, column_index))
                        for column_index in range(worksheet.ncols)
                        if worksheet.cell_value(row_index, column_index) != ""
                    ]
                    if values:
                        line = "\t".join(values)
                        parts.append(line)
                        length += len(line)
                    if length >= self.MAX_EXTRACTED_CHARACTERS:
                        return self._limit_text("\n".join(parts))
            return self._limit_text("\n".join(parts))
        finally:
            workbook.release_resources()

    def _extract_doc_text(self, filepath: Path) -> str:
        for executable_name in ("catdoc", "antiword"):
            executable = shutil.which(executable_name)
            if executable:
                result = subprocess.run(
                    [executable, str(filepath)],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return self._limit_text(result.stdout)

        libreoffice = self._find_libreoffice()
        if libreoffice is None:
            return ""

        with TemporaryDirectory(prefix="papagui-doc-index-") as temp_dir:
            output_dir = Path(temp_dir)
            result = subprocess.run(
                [
                    str(libreoffice),
                    "--headless",
                    "--convert-to",
                    "txt:Text",
                    "--outdir",
                    str(output_dir),
                    str(filepath),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            converted = next(output_dir.glob("*.txt"), None)
            if result.returncode == 0 and converted is not None:
                return self._limit_text(converted.read_text(encoding="utf-8", errors="replace"))
        return ""

    def _find_libreoffice(self) -> Optional[Path]:
        for name in ("libreoffice", "soffice"):
            found = shutil.which(name)
            if found:
                return Path(found)

        candidates = []
        if sys.platform == "darwin":
            candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
        elif sys.platform == "win32":
            for environment_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
                base = os.environ.get(environment_name)
                if base:
                    candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
        return next((path for path in candidates if path.exists()), None)
    
    def search_customers(self, query: str, limit: Optional[int] = None) -> List[Dict]:
        cursor = self.conn.cursor()
        
        q = f"%{query}%"
        sql = f"""
            SELECT
                {self.SEARCH_LABEL_SQL} AS customer_name,
                domain_folder AS service_type,
                time_bucket AS year,
                COUNT(*) AS file_count
            FROM files
            WHERE (
                {self.SEARCH_LABEL_SQL} LIKE ?
                OR relative_dir LIKE ?
                OR domain_folder LIKE ?
            )
            GROUP BY customer_name, service_type, year
            ORDER BY customer_name
        """
        params = [q, q, q]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor.execute(sql, params)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'customer_name': row['customer_name'],
                'service_type': row['service_type'],
                'year': row['year'],
                'file_count': row['file_count']
            })
        
        return results
    
    def get_customer_details(self, customer_name: str) -> Dict:
        cursor = self.conn.cursor()

        cursor.execute(
            f"""
            SELECT 
                COUNT(*) as file_count,
                MAX(modified_date) as last_modified,
                SUM(file_size) as total_size
            FROM files
            WHERE {self.SEARCH_LABEL_SQL} = ?
            """,
            (customer_name,)
        )
        
        info = dict(cursor.fetchone())
        
        cursor.execute(
            f"""
            SELECT DISTINCT domain_folder FROM files
            WHERE {self.SEARCH_LABEL_SQL} = ?
            ORDER BY domain_folder
            """,
            (customer_name,)
        )
        
        info['service_types'] = [row['domain_folder'] for row in cursor.fetchall() if row['domain_folder']]
        
        cursor.execute(
            f"""
            SELECT 
                path, filename, file_type, file_size, modified_date,
                domain_folder, time_bucket, project_name, subfolder, relative_dir,
                service_type, year
            FROM files
            WHERE {self.SEARCH_LABEL_SQL} = ?
            ORDER BY time_bucket DESC, domain_folder, subfolder, filename
            """,
            (customer_name,)
        )
        
        info['files'] = [dict(row) for row in cursor.fetchall()]
        info['customer_name'] = customer_name
        
        return info
    
    def search_files(
        self,
        query: str,
        customer_name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        cursor = self.conn.cursor()
        
        sql = "SELECT * FROM files WHERE (filename LIKE ? OR relative_dir LIKE ?)"
        params = [f"%{query}%"]
        params.append(f"%{query}%")
        
        if customer_name:
            sql += f" AND {self.SEARCH_LABEL_SQL} = ?"
            params.append(customer_name)
        
        sql += " ORDER BY time_bucket DESC, domain_folder, filename"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def _iter_text_search_targets(self, customer_name: Optional[str]) -> List[List[str]]:
        cursor = self.conn.cursor()

        if customer_name:
            cursor.execute(
                f"SELECT DISTINCT path FROM files WHERE {self.SEARCH_LABEL_SQL} = ?",
                (customer_name,)
            )
            paths = [row[0] for row in cursor.fetchall()]
            chunk_size = 200
            return [paths[i:i + chunk_size] for i in range(0, len(paths), chunk_size)]

        cursor.execute("SELECT DISTINCT root_path FROM indexed_roots")
        roots = [row[0] for row in cursor.fetchall()]
        return [[root] for root in roots]

    def _parse_rg_output(self, stdout: str) -> List[Dict]:
        parsed = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data", {})
            path_data = data.get("path", {})
            lines_data = data.get("lines", {})
            file_path = path_data.get("text")
            if not file_path:
                continue
            parsed.append({
                "path": file_path,
                "line": data.get("line_number"),
                "excerpt": (lines_data.get("text") or "").strip(),
                "source": "text",
            })
        return parsed

    def _build_fts_query(self, query: str) -> str:
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)

    def _search_extracted_content(
        self,
        query: str,
        customer_name: Optional[str],
        limit: int = 200,
    ) -> List[Dict]:
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        sql = f"""
            SELECT
                file_content_fts.path AS path,
                snippet(file_content_fts, 1, '', '', ' … ', 18) AS excerpt
            FROM file_content_fts
            JOIN files ON files.path = file_content_fts.path
            WHERE file_content_fts MATCH ?
        """
        params = [fts_query]
        if customer_name:
            sql += f" AND {self.SEARCH_LABEL_SQL} = ?"
            params.append(customer_name)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return [
            {
                "path": row["path"],
                "line": None,
                "excerpt": (row["excerpt"] or "").replace("\n", " ").strip(),
                "source": "document",
            }
            for row in cursor.fetchall()
        ]
    
    def _run_ripgrep(
        self,
        command: List[str],
        should_cancel: Optional[Callable[[], bool]],
    ) -> str:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        deadline = time.monotonic() + 25
        while True:
            try:
                stdout, _ = process.communicate(timeout=0.1)
                return stdout
            except subprocess.TimeoutExpired:
                if should_cancel is not None and should_cancel():
                    process.terminate()
                    try:
                        process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    return ""
                if time.monotonic() >= deadline:
                    process.kill()
                    process.communicate()
                    return ""

    def search_in_text(
        self,
        query: str,
        customer_name: Optional[str] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> List[Dict]:
        try:
            results = self._search_extracted_content(query, customer_name)
            if should_cancel is not None and should_cancel():
                return results

            rg_path = shutil.which("rg")
            if rg_path is None:
                return results

            target_groups = self._iter_text_search_targets(customer_name)
            if not target_groups:
                return results

            for targets in target_groups:
                if should_cancel is not None and should_cancel():
                    break
                command = [
                    rg_path,
                    '--json',
                    '--line-number',
                    '--no-messages',
                    '--smart-case',
                    '--fixed-strings',
                    '--glob', '*.txt',
                    '--glob', '*.csv',
                    '--glob', '*.md',
                    '--glob', '*.log',
                    '--glob', '*.json',
                    '--glob', '*.xml',
                    '--glob', '*.yaml',
                    '--glob', '*.yml',
                    '--glob', '*.ini',
                    query,
                    *targets,
                ]
                stdout = self._run_ripgrep(command, should_cancel)
                if stdout:
                    results.extend(self._parse_rg_output(stdout))

            unique_results = []
            seen = set()
            for result in results:
                key = (result["path"], result.get("line"))
                if key in seen:
                    continue
                seen.add(key)
                unique_results.append(result)
                if len(unique_results) >= 200:
                    break
            return unique_results
        except Exception as e:
            print(f"Fehler bei Volltextsuche: {e}")
            return []
    
    def close(self):
        """Schließt die Datenbankverbindung"""
        if self.conn:
            self.conn.close()


if __name__ == "__main__":
    from app.core.config import DB_FILE, get_default_index_source
    
    manager = IndexManager(DB_FILE)
    index_root = get_default_index_source()
    manager.index_directory(index_root)
    
    results = manager.search_customers("Müller")
    print(f"Gefundene Kunden: {results}")
    
    manager.close()
