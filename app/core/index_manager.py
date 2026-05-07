import sqlite3
from pathlib import Path
from datetime import datetime
import os
from typing import List, Dict, Tuple, Optional
import subprocess
import shutil


class IndexManager:
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
                full_text_indexed BOOLEAN DEFAULT 0
            )
        """)
        
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
        
        self.conn.commit()
    
    def index_directory(self, base_path: Path):
        cursor = self.conn.cursor()
        indexed_count = 0
        
        for filepath in base_path.rglob('*'):
            if filepath.is_file():
                try:
                    self._index_file(filepath, base_path, cursor)
                    indexed_count += 1
                except Exception as e:
                    print(f"Fehler beim Indexieren von {filepath}: {e}")
        
        self.conn.commit()
        print(f"✓ {indexed_count} Dateien indiziert")
        return indexed_count
    
    def _index_file(self, filepath: Path, base_path: Path, cursor: sqlite3.Cursor):
        try:
            stat = filepath.stat()
            rel_path = str(filepath.relative_to(base_path))
            parts = rel_path.split(os.sep)
            
            year = parts[0] if len(parts) > 0 else None
            service_type = parts[1] if len(parts) > 1 else None
            customer_name = parts[2] if len(parts) > 2 else None
            subfolder = parts[3] if len(parts) > 3 else None
            
            file_type = filepath.suffix.lower().lstrip('.')
            
            cursor.execute("""
                INSERT OR REPLACE INTO files 
                (path, filename, file_size, file_type, created_date, modified_date, 
                 year, service_type, customer_name, subfolder)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                subfolder
            ))
        except Exception as e:
            print(f"Fehler beim Indexieren von {filepath}: {e}")
    
    def search_customers(self, query: str) -> List[Dict]:
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT customer_name, service_type, year
            FROM files
            WHERE customer_name LIKE ?
            ORDER BY customer_name
        """, (f"%{query}%",))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'customer_name': row['customer_name'],
                'service_type': row['service_type'],
                'year': row['year']
            })
        
        return results
    
    def get_customer_details(self, customer_name: str) -> Dict:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as file_count,
                MAX(modified_date) as last_modified,
                SUM(file_size) as total_size
            FROM files
            WHERE customer_name = ?
        """, (customer_name,))
        
        info = dict(cursor.fetchone())
        
        # Service-Types und Jahre
        cursor.execute("""
            SELECT DISTINCT service_type FROM files
            WHERE customer_name = ?
            ORDER BY service_type
        """, (customer_name,))
        
        info['service_types'] = [row['service_type'] for row in cursor.fetchall()]
        
        # Alle Dateien des Kunden
        cursor.execute("""
            SELECT 
                path, filename, file_type, file_size, modified_date,
                service_type, year, subfolder
            FROM files
            WHERE customer_name = ?
            ORDER BY year DESC, service_type, subfolder, filename
        """, (customer_name,))
        
        info['files'] = [dict(row) for row in cursor.fetchall()]
        info['customer_name'] = customer_name
        
        return info
    
    def search_files(self, query: str, customer_name: Optional[str] = None) -> List[Dict]:
        """Sucht Dateien nach Filenamen"""
        cursor = self.conn.cursor()
        
        sql = "SELECT * FROM files WHERE filename LIKE ?"
        params = [f"%{query}%"]
        
        if customer_name:
            sql += " AND customer_name = ?"
            params.append(customer_name)
        
        sql += " ORDER BY customer_name, year, service_type"
        
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def search_in_text(self, query: str, customer_name: Optional[str] = None) -> List[Tuple[Path, int]]:
        """
        Sucht nach Text in Dateien mit ripgrep.
        Returns: Liste von (filepath, line_number) Tupeln
        """
        if not shutil.which('rg'):
            print("⚠ ripgrep nicht installiert. Bitte installieren: apt install ripgrep")
            return []
        
        try:
            # Filterung nach Kundenname wenn angegeben
            if customer_name:
                cursor = self.conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT path FROM files
                    WHERE customer_name = ?
                """, (customer_name,))
                file_paths = [Path(row['path']) for row in cursor.fetchall()]
            else:
                cursor = self.conn.cursor()
                cursor.execute("SELECT DISTINCT path FROM files")
                file_paths = [Path(row['path']) for row in cursor.fetchall()]
            
            results = []
            for filepath in file_paths:
                try:
                    # ripgrep mit verschiedenen Dateitypen
                    result = subprocess.run(
                        ['rg', query, str(filepath), '--line-number', '-i'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    
                    if result.stdout:
                        for line in result.stdout.strip().split('\n'):
                            if ':' in line:
                                line_num = line.split(':')[1]
                                try:
                                    results.append((filepath, int(line_num)))
                                except ValueError:
                                    pass
                except subprocess.TimeoutExpired:
                    pass
            
            return results
        except Exception as e:
            print(f"Fehler bei Volltextsuche: {e}")
            return []
    
    def close(self):
        """Schließt die Datenbankverbindung"""
        if self.conn:
            self.conn.close()


if __name__ == "__main__":
    from app.core.config import DB_FILE, MOCK_DATA_DIR
    
    # Beispielnutzung
    manager = IndexManager(DB_FILE)
    manager.index_directory(MOCK_DATA_DIR)
    
    # Suche testen
    results = manager.search_customers("Müller")
    print(f"Gefundene Kunden: {results}")
    
    manager.close()
