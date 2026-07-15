import sqlite3
from pathlib import Path
from datetime import datetime
import os
from typing import List, Dict, Tuple, Optional
import subprocess
import shutil


class IndexManager:
    SEARCH_LABEL_SQL = "COALESCE(NULLIF(project_name, ''), NULLIF(customer_name, ''), filename)"

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = None
        self.init_db()
    
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
    
    def index_directory(self, base_path: Path):
        if not base_path.exists():
            raise FileNotFoundError(f"Index-Pfad existiert nicht: {base_path}")

        cursor = self.conn.cursor()
        indexed_count = 0
        root = str(base_path)

        cursor.execute("DELETE FROM files WHERE index_root = ?", (root,))
        cursor.execute("DELETE FROM files WHERE path LIKE ?", (f"{root}{os.sep}%",))
        
        for filepath in base_path.rglob('*'):
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
                 domain_folder, time_bucket, project_name, relative_dir, index_root)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                str(base_path)
            ))
        except Exception as e:
            print(f"Fehler beim Indexieren von {filepath}: {e}")
    
    def search_customers(self, query: str) -> List[Dict]:
        cursor = self.conn.cursor()
        
        q = f"%{query}%"
        cursor.execute(
            f"""
            SELECT
                {self.SEARCH_LABEL_SQL} AS customer_name,
                domain_folder AS service_type,
                time_bucket AS year,
                COUNT(*) AS file_count
            FROM files
            WHERE (
                {self.SEARCH_LABEL_SQL} LIKE ?
                OR filename LIKE ?
                OR relative_dir LIKE ?
                OR path LIKE ?
            )
            GROUP BY customer_name, service_type, year
            ORDER BY customer_name
            """,
            (q, q, q, q)
        )
        
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
    
    def search_files(self, query: str, customer_name: Optional[str] = None) -> List[Dict]:
        cursor = self.conn.cursor()
        
        sql = "SELECT * FROM files WHERE (filename LIKE ? OR relative_dir LIKE ?)"
        params = [f"%{query}%"]
        params.append(f"%{query}%")
        
        if customer_name:
            sql += f" AND {self.SEARCH_LABEL_SQL} = ?"
            params.append(customer_name)
        
        sql += " ORDER BY time_bucket DESC, domain_folder, filename"
        
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

    def _parse_rg_output(self, stdout: str) -> List[Tuple[Path, int]]:
        parsed: List[Tuple[Path, int]] = []
        for line in stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            file_path, line_num, _ = parts
            try:
                parsed.append((Path(file_path), int(line_num)))
            except ValueError:
                continue
        return parsed
    
    def search_in_text(self, query: str, customer_name: Optional[str] = None) -> List[Tuple[Path, int]]:
        if not shutil.which('rg'):
            print("⚠ ripgrep nicht installiert. Bitte installieren: apt install ripgrep")
            return []
        
        try:
            target_groups = self._iter_text_search_targets(customer_name)
            if not target_groups:
                return []

            results: List[Tuple[Path, int]] = []
            for targets in target_groups:
                command = [
                    'rg',
                    '--line-number',
                    '--ignore-case',
                    '--no-messages',
                    '--smart-case',
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
                try:
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=25
                    )
                    if result.stdout:
                        results.extend(self._parse_rg_output(result.stdout))
                except subprocess.TimeoutExpired:
                    continue

            return results
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
