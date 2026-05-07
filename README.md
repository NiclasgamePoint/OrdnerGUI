## Funktionen

### Implementiert (v0.1)
- **Kundensuche**: Suche nach Kundennamen mit lokaler Indexierung
- **Dateiindex**: SQLite-basierter Index für schnelle Metadaten-Abfragen
- **Dateisystem-Browser**: Ordnerstruktur: `[Jahr]/[Dienstleistungstyp]/[Kundenname]/[Subfolder]`
- **Multi-Format Viewer**:
  - PDFs (Basis + Text-Extract)
  - Bilder (JPG, PNG, GIF, BMP)
  - Excel-Dateien (XLSX/XLS) - Tabellenvorschau
  - Textdateien (TXT, CSV, LOG, MD)
  - Word-Dokumente (DOCX)
- **Volltextsuche**: ripgrep-Integration für Textdateien
- **Mock-Daten**: 5 Kunden × 50 Dateien zum Testen
- **GUI**: PySide6-basiert mit modernem Interface

### Zu Implementieren (später)
- Reiter für verschiedene Kundenviews
- PDF-Vorschau (vollständig, nicht nur Text)
- Thumbnails für Bilder
- Favoriten/Lesezeichen
- NAS-Anbindung und Authentifizierung
- Erweiterte Filter (Datum, Dateityp, Größe)
- Export-Funktionen
- Backup-Management
- Logging & Debugging-Tools

## Installation

### 1. Repository klonen
```bash
git clone https://github.com/NiclasgamePoint/OrdnerGUI.git
```

### 2. Virtuelle Umgebung erstellen
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Abhängigkeiten installieren
```bash
pip install -r requirements.txt
```

### 4. Zusätzliche Abhängigkeiten (optional)
```bash
# Für Volltextsuche (ripgrep)
sudo apt install ripgrep

# Für PDF-Extraktion (optional)
pip install PyPDF2
```

### Mock-Daten neu generieren
```bash
python -c "from app.core.mock_data_generator import generate_mock_data; from app.core.config import MOCK_DATA_DIR; generate_mock_data(MOCK_DATA_DIR, 5, 50)"
```

### Index zurücksetzen
```bash
rm data/index.db
python main.py  # Index wird automatisch neu erstellt
```

## Projektstruktur

```
PapaGUI/
├── main.py                      # Entry Point
├── requirements.txt             # Python-Abhängigkeiten
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py           # Konfiguration
│   │   ├── index_manager.py    # SQLite-Index & Suche
│   │   └── mock_data_generator.py  # Test-Datengenerator
│   └── gui/
│       ├── __init__.py
│       ├── main_window.py      # Hauptfenster
│       └── viewer.py           # Datei-Viewer
└── data/
    ├── mock/                   # Mock-Kundendaten
    └── index.db               # SQLite-Index (wird auto-erstellt)
```

## Architektur

### 1. **Dateiindexierung**
- SQLite-Datenbank speichert Metadaten (Pfad, Dateiname, Größe, Datum, Kunde, etc.)
- Automatische Indizierung beim Start (wenn nötig)
- Ordnersystem bleibt als "Source of Truth"

### 2. **Suchstrategie**
- **Datei-/Pfadsuche**: SQL-Queries gegen lokalen Index
- **Volltextsuche**: ripgrep CLI für Text-Dateien (schnell & robust)

### 3. **GUI (PySide6)**
- Linke Seite: Kundensuche & -liste
- Rechte Seite: Kundendetails + Datei-Viewer (Tabs)
- Status-Bar für Rückmeldungen

