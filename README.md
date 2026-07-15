## Datenstruktur

Die Indexierung behandelt alle Fachthemen gleich. Es gibt keine Sonderlogik für „Blower Door“.

```text
Bauvorhaben/
├── Baubegleitung/
├── Blower Door/
├── DEKRA/
└── Elektroplanung/
    └── Jahr oder Vorlagen/
        └── Projekt- bzw. Kundenordner/
            └── weitere Unterordner und Dateien
```

Weitere Fachthemen können als neue Ordner der ersten Ebene ergänzt werden und werden automatisch erkannt.

## Funktionen

- Gemeinsame Suche nach Ordnern/Kunden, Dateinamen und Dokumentinhalten
- Live-Ordnersuche beim Tippen; parallele vollständige Suche mit Enter oder „Suchen“
- Filter nach Fachthema, Jahr/Vorlagen und Dateityp
- Relevanzranking, hervorgehobene Treffer, getrennte Pagination und Suchverlauf
- SQLite-Metadatenindex und FTS5-Volltextindex
- Textextraktion aus PDF, DOC/DOCX, XLS/XLSX und üblichen Textformaten
- Optionaler OCR-Fallback für gescannte PDFs
- Inkrementelle Indexierung im Hintergrund
- Sicherer Indexaufbau in einer temporären Datenbank, atomarer Wechsel und drei Sicherungen
- Automatische, plattformübergreifende Überwachung der Datenquelle
- Indexdiagnose mit Integrität, Laufzeit, Datei-/Ordnerzahlen und Extraktionsfehlern
- PDF-Viewer mit Seitensteuerung, Zoom und Suche
- Excel-Viewer mit Tabellenblättern und Zellensuche
- Text-/Word-Viewer mit Suche sowie externes Öffnen von Datei oder Ordner
- Separate Kundenverwaltung mit Entität, Stammdaten, Kontakten, Notizen und Tags
- Light-/Dark-Theme und frei wählbare Akzentfarbe

## Architektur

```text
app/
├── core/
│   ├── config.py                 Konfiguration und persistente Optionen
│   ├── index_manager.py          Indexierung, Extraktion und Suche
│   ├── index_store.py            Staging, Validierung und Indexgenerationen
│   ├── index_diagnostics.py      Lesbare Index-Zustandsberichte
│   ├── search_models.py          Filter, Seitenmodell und Suchverlauf
│   ├── customer_models.py        Kunden- und Kontaktmodelle
│   └── customer_repository.py    Separate Kunden-Datenbank
├── services/
│   ├── filesystem_monitor.py     Hintergrundüberwachung der Datenquelle
│   └── document_converter.py     Optionale Legacy-Konvertierung
└── gui/
    ├── dialogs/                  Kundendaten-Editor
    ├── panels/                   Ordner- und Kundendetails
    ├── viewers/                  PDF-, Tabellen- und Textviewer
    ├── widgets/                  Wiederverwendbare Buttons und Suchwidgets
    ├── workers/                  Such- und Index-Threads
    ├── main_window.py
    ├── settings_popup.py
    └── theme.py
```

`data/index.db` ist austauschbar und kann jederzeit neu erzeugt werden. `data/customers.db` enthält die manuell gepflegten Kundendaten und wird bei einer Neuindexierung nicht verändert.

## Start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Unter Windows werden entsprechend `.venv\Scripts\pip.exe` und `.venv\Scripts\python.exe` verwendet. Die Anwendung nicht mit `sudo` starten.

Optionale Systemprogramme:

- `ripgrep` als Textsuch-Fallback
- Tesseract und Poppler für OCR
- LibreOffice sowie `catdoc` oder `antiword` für alte Office-Dateien

Fehlende optionale Programme verhindern den normalen Start nicht.

## Tests

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

Die Tests prüfen unter anderem generische Fachthemen, inkrementelle Indexierung, Diagnosewerte, Filter/Pagination, Dateisystemänderungen, Viewer und Kunden-CRUD.
