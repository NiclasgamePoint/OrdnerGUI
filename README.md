## Vorschläge

[Vorschläge und Dinge die ich noch tun wollen würde](Vorschlaege.md)

## Funktionen

- Versionierung über `app.__version__` und `pyproject.toml`

- Gemeinsame, scrollbare Suchübersicht für Kunden und Ordner
- Parallele Kunden- und Ordnersuche beim Tippen sowie mit Enter oder „Suchen“
- Filter nach Fachthema, Jahr/Vorlagen und Dateityp
- Relevanzranking, hervorgehobene Treffer, getrennte Pagination und Suchverlauf
- Sofort nutzbarer SQLite-Katalog und progressive, jahresbasierte FTS5-Inhaltsshards
- Textextraktion aus PDF, DOC/DOCX, XLS/XLSX und üblichen Textformaten
- Optionaler OCR-Fallback für gescannte PDFs
- Fortsetzbare Dokumentextraktion in einem unabhängigen Hintergrundprozess
- Indexläufe laufen als eigener Prozess auch nach dem Schließen der GUI weiter
- Sicherer Katalogaufbau, atomarer Wechsel und drei kleine Katalogsicherungen
- Native Dateisystemüberwachung plus täglicher vollständiger Sicherheitsabgleich
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
│   ├── index_manager.py          Gemeinsame Metadaten- und Legacy-Suchoperationen
│   ├── catalog_index.py          Schneller Katalogaufbau und Kataloggenerationen
│   ├── content_index.py          Queue, komprimierte FTS-Shards und Wartung
│   ├── content_search.py         Progressive globale Mehr-Shard-Suche
│   ├── index_layout.py           Zentrale Pfade der geteilten Indexstruktur
│   ├── index_diagnostics.py      Lesbare Index-Zustandsberichte
│   ├── search_models.py          Filter, Seitenmodell und Suchverlauf
│   ├── customer_models.py        Kunden- und Kontaktmodelle
│   ├── folder_structure.py       Erkennung kanonischer Projektwurzeln
│   └── customer_repository.py    Separate Kunden-Datenbank
├── services/
│   ├── filesystem_monitor.py     Hintergrundüberwachung der Datenquelle
│   ├── content_index_job.py      Fortsetzbarer Inhaltsindexprozess
│   ├── content_index_worker.py   Sequenzielle Extraktionspipeline
│   ├── document_text_indexer.py  Formatunabhängige Textextraktion
│   ├── customer_recognition.py   Sichere automatische Kundenzuordnung
│   └── document_converter.py     Optionale Legacy-Konvertierung
└── gui/
    ├── dialogs/                  Kundendaten-Editor
    ├── pages/                    Suche, Kundendetails und Ordner-/Viewer-Seite
    ├── panels/                   Wiederverwendbare Panel-Bausteine (reserviert)
    ├── viewers/                  PDF-, Tabellen- und Textviewer
    ├── widgets/                  App-Rahmen, Ergebniszeilen und Suchwidgets
    ├── workers/                  Suche und Steuerung des Indexprozesses
    ├── navigation.py             Seitenverlauf und Zurück-Navigation
    ├── main_window.py
    ├── settings_popup.py
    └── theme.py
```

Die abgeleiteten Indexdaten liegen unter `data/index/`: `catalog/active.db`
enthält ausschließlich Datei-, Ordner- und Projektmetadaten. Extrahierte Texte
liegen zlib-komprimiert in höchstens ungefähr 1 GiB großen Jahres-Shards unter
`content/shards/`; `content/state.db` hält die fortsetzbare Warteschlange. Der
Katalog wird zuerst aktiviert, sodass Datei- und Ordnersuche sofort verfügbar
sind. Die Dokumentinhaltssuche zeigt während des Hintergrundaufbaus ihren
Abdeckungsgrad und liefert Treffer aus allen bereits fertigen Shards.

Nur der Katalog besitzt drei rotierende Sicherungen. Inhaltsshards sind
rekonstruierbar. Beim ersten erfolgreichen Wechsel wird der alte v0.2-Index nach
`data/index/legacy-v0.2/` verschoben. `data/customers.db` bleibt davon getrennt
und enthält die dauerhaften Kundendaten. Manuell gepflegte Werte werden durch
die zweistufige Kundenerkennung nicht überschrieben.

Der Git-Tag `v0.2` markiert den unveränderten Stand vor der Indexaufteilung.
Für eine Rückkehr sollte die Anwendung auf diesen Tag zurückgesetzt und der
archivierte `index.db` aus `data/index/legacy-v0.2/` wieder nach `data/` kopiert
werden; die Kundendatenbank ist davon nicht betroffen.

## Start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Unter Windows werden entsprechend `.venv\Scripts\pip.exe` und `.venv\Scripts\python.exe` verwendet. Die Anwendung nicht mit `sudo` starten.

Alternative Startskripte:

- Linux: `start.sh`
- macOS: `start.command`
- Windows CMD: `start.bat`
- Windows PowerShell: `start.ps1`

Optionale Systemprogramme:

- Tesseract und Poppler für OCR
- LibreOffice sowie `catdoc` oder `antiword` für alte Office-Dateien

Fehlende optionale Programme verhindern den normalen Start nicht.

Für reproduzierbare Builds steht zusätzlich `requirements-lock.txt` mit exakt gepinnten Versionen bereit.

## Tests

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

Die Tests prüfen unter anderem Katalogaktivierung, Queue-Fortsetzung,
Jahres-Shard-Rollover, progressive Mehr-Shard-Suche, inkrementelle Indexierung,
Diagnosewerte, Dateisystemänderungen, Viewer und Kunden-CRUD.

## Kundenvorschläge und Kundenübersicht

Die Anwendung unterstützt eine Kundenverwaltung, die von einzelnen Dienstleistungen getrennt ist. Ein Kunde kann mehrere Dienstleistungen und mehrere verknüpfte Projektordner haben.

- Kundentypen sind dynamisch und können beim Bearbeiten frei gepflegt werden (z. B. Privatkunde, Firma, Gemeinde).
- Kundendaten können über Vorschläge aus Ordnerstruktur und Dokumentinhalten vorbereitet und anschließend im Dialog bestätigt werden.
- Die automatische Erkennung ist standardmäßig deaktiviert und muss unter „Kundenerkennung“ ausdrücklich eingeschaltet werden.
- Projektordner mit demselben normalisierten Kundennamen werden automatisch einem gemeinsamen Kunden zugeordnet. Bereits vorhandene Namensdubletten werden beim Öffnen der Kundendatenbank verlustfrei zusammengeführt und intern protokolliert.
- Nur ähnliche Namen oder widersprüchliche bestehende Ordnerzuordnungen werden dauerhaft zur manuellen Prüfung vorgemerkt.
- Unterhalb des Hauptbereichs gibt es eine Kundenübersicht mit Kundenliste (links) und dynamischer Detailansicht (rechts).

Die Übersicht ist Teil der normalen Seitenansicht und über vertikales Scrollen erreichbar.
