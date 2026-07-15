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
│   └── gui/
│       ├── __init__.py
│       ├── main_window.py      # Hauptfenster
│       └── viewer.py           # Datei-Viewer
├── Bauvorhaben/               # Reale Projektdaten (Indexquelle)
└── data/
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

