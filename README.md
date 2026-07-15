## Funktionen

### Implementiert (v0.1)
- **Kundensuche**: Suche nach Kundennamen mit lokaler Indexierung
- **Dateiindex**: SQLite-basierter Index für schnelle Metadaten-Abfragen
- **Dateisystem-Browser**: Ordnerstruktur: `[Dienstleistungstyp]/[Jahr]/[Kundenname & Ort]/[Subfolder]`
- **Multi-Format Viewer**:
  - PDFs (Basis + Text-Extract)
  - Bilder (JPG, PNG, GIF, BMP)
  - Excel-Dateien (XLSX/XLS) - Tabellenvorschau
  - Textdateien (TXT, CSV, LOG, MD)
  - Word-Dokumente (DOCX)
- **Volltextsuche**: ripgrep-Integration für Textdateien
- **Lokaler Inhaltsindex**: SQLite FTS5 für PDF-, Word-, Excel- und Textdateien
- **Inkrementelle Indexierung**: Verarbeitet nur neue, geänderte oder gelöschte Dateien
- **Sichere Indexgenerationen**: Aufbau in einer separaten Datei mit drei Backups
- **OCR-Fallback**: Optional für gescannte PDFs (Tesseract + Poppler)
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
- Automatischer inkrementeller Abgleich beim Start
- Dokumentextrakte und normale Textdateien werden lokal mit FTS5 indexiert
- Ein neuer Index wird separat aufgebaut und erst nach erfolgreicher Prüfung aktiviert
- Bis zu drei ältere Indexstände können über die Einstellungen geladen werden
- Ordnersystem bleibt als "Source of Truth"

### 2. **Suchstrategie**
- **Datei-/Pfadsuche**: SQL-Queries gegen lokalen Index
- **Volltextsuche**: ripgrep CLI für Text-Dateien (schnell & robust)

### 3. **GUI (PySide6)**
- Linke Seite: Kundensuche & -liste
- Rechte Seite: Kundendetails + Datei-Viewer (Tabs)
- Status-Bar für Rückmeldungen



## Vorschläge

### Papa
- Dateipfad im Finder öffnen für Papa
    - damit man es in den Mails finden kann
- Imap server mit einbinden
- Automatische indizierung

