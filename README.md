## Vorschläge

[Vorschläge und Dinge die ich noch tun wollen würde](Vorschlaege.md)

## Funktionen

- Gemeinsame, scrollbare Suchübersicht für Kunden und Ordner
- Parallele Kunden- und Ordnersuche beim Tippen sowie mit Enter oder „Suchen“
- Filter nach Fachthema, Jahr/Vorlagen und Dateityp
- Relevanzranking, hervorgehobene Treffer, getrennte Pagination und Suchverlauf
- SQLite-Metadatenindex und FTS5-Volltextindex
- Textextraktion aus PDF, DOC/DOCX, XLS/XLSX und üblichen Textformaten
- Optionaler OCR-Fallback für gescannte PDFs
- Inkrementelle Indexierung im Hintergrund
- Indexläufe laufen als eigener Prozess auch nach dem Schließen der GUI weiter
- Sicherer Indexaufbau in einer temporären Datenbank, atomarer Wechsel und drei Sicherungen
- Automatische, plattformübergreifende Überwachung der Datenquelle
- Indexdiagnose mit Integrität, Laufzeit, Datei-/Ordnerzahlen und Extraktionsfehlern
- Optionale automatische Kundenerkennung für `Dienstleistung/Jahr/Nachname, Ort` ab 2016
- Persistente Blacklists und Prüfwarteschlange für mehrdeutige Kundenzuordnungen
- Kanonische Projektordner ohne doppelte Unterordner-Treffer
- Aufklappbare Ordner- und Dateistruktur mit rekursivem Kundenbezug
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
│   ├── folder_structure.py       Erkennung kanonischer Projektwurzeln
│   └── customer_repository.py    Separate Kunden-Datenbank
├── services/
│   ├── filesystem_monitor.py     Hintergrundüberwachung der Datenquelle
│   ├── customer_recognition.py   Sichere automatische Kundenzuordnung
│   └── document_converter.py     Optionale Legacy-Konvertierung
└── gui/
    ├── dialogs/                  Kundendaten-Editor
    ├── pages/                    Suche, Kundendetails und Ordner-/Viewer-Seite
    ├── viewers/                  PDF-, Tabellen- und Textviewer
    ├── widgets/                  App-Rahmen, Ergebniszeilen und Suchwidgets
    ├── workers/                  Suche und Steuerung des Indexprozesses
    ├── navigation.py             Seitenverlauf und Zurück-Navigation
    ├── main_window.py
    ├── settings_popup.py
    └── theme.py
```

`data/index.db` ist austauschbar und kann jederzeit neu erzeugt werden. `data/customers.db` enthält die dauerhaften Kundendaten. Bei deaktivierter Kundenerkennung bleibt sie durch Indexläufe unverändert; bei aktivierter Erkennung werden ausschließlich leere Felder, Dienstleistungen und Projektordner ergänzt. Manuell gepflegte Werte werden nicht überschrieben.

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

## Kundenvorschläge und Kundenübersicht

Die Anwendung unterstützt eine Kundenverwaltung, die von einzelnen Dienstleistungen getrennt ist. Ein Kunde kann mehrere Dienstleistungen und mehrere verknüpfte Projektordner haben.

- Kundentypen sind dynamisch und können beim Bearbeiten frei gepflegt werden (z. B. Privatkunde, Firma, Gemeinde).
- Kundendaten können über Vorschläge aus Ordnerstruktur und Dokumentinhalten vorbereitet und anschließend im Dialog bestätigt werden.
- Die automatische Erkennung ist standardmäßig deaktiviert und muss unter „Kundenerkennung“ ausdrücklich eingeschaltet werden.
- Mehrdeutige Treffer werden nicht automatisch zusammengeführt, sondern dauerhaft zur manuellen Prüfung vorgemerkt.
- Unterhalb des Hauptbereichs gibt es eine Kundenübersicht mit Kundenliste (links) und dynamischer Detailansicht (rechts).

Die Übersicht ist Teil der normalen Seitenansicht und über vertikales Scrollen erreichbar.
