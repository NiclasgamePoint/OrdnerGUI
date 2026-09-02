## Vorschläge

[Vorschläge und Dinge die ich noch tun wollen würde](Vorschlaege.md)

## Funktionen

- Versionierung über `app.__version__` und `pyproject.toml`
- Docker-Indexserver als Single Source of Truth mit geprüften Generationen
- automatischer Client-Synchronisation, drei Backups und Offline-Fallback
- revisionierter Kunden-API ohne stilles Überschreiben paralleler Änderungen
- zentraler Indexserver-Konsole im Systemtray für Live-Status, Wartung,
  Docker-Neustart und persistente serverseitige Indexeinstellungen

- Gemeinsame, scrollbare Suchübersicht für Kunden und Ordner
- Parallele Kunden- und Ordnersuche beim Tippen sowie mit Enter oder „Suchen“
- Filter nach Fachthema, Jahr/Vorlagen und Dateityp
- Relevanzranking, hervorgehobene Treffer, getrennte Pagination und Suchverlauf
- Sofort nutzbarer SQLite-Katalog und progressive, jahresbasierte FTS5-Inhaltsshards
- Parallele Textextraktion aus PDF, DOC/DOCX, XLS/XLSX und üblichen Textformaten
- Poppler-`pdftotext` mit isoliertem pypdf-Fallback und festem Zeitlimit
- Zweistufiger OCR-Fallback für gescannte PDFs
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
- Verzögerte kontextbezogene Erklärungen für alle Einstellungsfelder und -aktionen

## Architektur

```text
app/
├── index_tray_app.py              Eigenständiger Prozess für Server-Tray und -Konsole
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
│   ├── content_index_worker.py   Parallele Extraktion, serieller Shard-Writer
│   ├── document_text_indexer.py  Formatstrategien, PDF-Fallback und OCR
│   ├── index_service.py           Headless-Orchestrierung für Docker/Synology
│   ├── index_resource_policy.py  CPU-/RAM-basierte Workerbegrenzung
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
    ├── index_tray_window.py         Serverstatus, Indexaktionen und Einstellungen
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

Unter „Einstellungen → Indexierung“ lassen sich die automatische
Katalogüberwachung, die fortsetzbare Dateiindizierung, Dateiformate,
Extraktionsgrenzen, OCR, Ressourcenprofil und Queue-Priorisierung getrennt
steuern. Pause und Fortsetzung bleiben über Programmneustarts erhalten.
Diagnose und Wartungsaktionen betreffen ausschließlich rekonstruierbare
Indexdaten. Suchtrefferlimit, Dokumentinhaltssuche und parallele Shard-Suche
befinden sich getrennt unter „Einstellungen → Suche“.

Alle konfigurierbaren Felder und Einstellungsaktionen besitzen eine kurze
kontextbezogene Hilfe. Bleibt die Maus drei Sekunden über einem Feld oder hält
es drei Sekunden den Tastaturfokus, erscheint eine passive Hilfeblase direkt am
Element. Sie bleibt bis zum Verlassen beziehungsweise Fokuswechsel sichtbar
und erklärt neben der Funktion auch wichtige Folgen wie Neuindizierung,
Ressourcenverbrauch oder Datenlöschung.

Neue Installationen starten mit maximal 100 MB Dokumentgröße und fünf
OCR-Seiten in der ersten Stufe. Liefert diese Stufe weniger als 500 Zeichen,
werden bei Bedarf bis zu 25 Seiten verarbeitet. `pdftotext` wird für PDFs
bevorzugt; liefert es keinen Text, läuft pypdf in einem isolierten Prozess als
Fallback. Für die PDF-Texterkennung gilt standardmäßig ein hartes Zeitlimit von
45 Sekunden. Ein Timeout wird im normalen Lauf nicht sofort wiederholt. Solche
Dokumente können später gezielt über „Fehler erneut versuchen“ freigegeben
werden.

Die Ressourcenprofile vergeben ungefähr 15 % (Schonend), 25 % (Ausgewogen)
oder 60 % (Schnell) der logischen CPUs an Dokument-Worker. Zusätzlich bleiben
mindestens 2 GB beziehungsweise 25 % des gesamten Arbeitsspeichers frei; pro
Worker werden 256 MB eingeplant und insgesamt höchstens 20 Worker gestartet.
Extraktionen laufen parallel, sämtliche SQLite-/FTS-Schreibvorgänge dagegen
über einen einzelnen Writer. Dadurch können schwierige Dokumente andere
Extraktionen nicht blockieren, ohne konkurrierende Schreibzugriffe auf einen
Jahres-Shard zu erzeugen.

Das rotierende Log `data/logs/content-index-process.log` enthält pro Dokument
Dateityp, Größe, Seitenzahl, Parser, Status, Zeichenanzahl, Gesamt-/Parser-/OCR-
Dauer und OCR-Seiten. Am Ende eines Laufabschnitts folgen Durchsatz,
Parserverteilung, Timeout-Anzahl und die zehn langsamsten Dokumente; extrahierte
Dokumenttexte werden nicht protokolliert.

Das Index-Trayfenster zeigt während des Inhaltsaufbaus zusätzlich das aktive
Ressourcenprofil, belegte und maximal verfügbare Worker sowie für jeden
logischen Worker den aktuell bearbeiteten, kopierbaren Dateipfad.

Nur der Katalog besitzt drei rotierende Sicherungen. Inhaltsshards sind
rekonstruierbar. Beim ersten erfolgreichen Wechsel wird der alte v0.2-Index nach
`data/index/legacy-v0.2/` verschoben. `data/customers.db` bleibt davon getrennt
und enthält die dauerhaften Kundendaten. Manuell gepflegte Werte werden durch
die zweistufige Kundenerkennung nicht überschrieben.

Das Verzeichnis `legacy-v0.2` bezeichnet das frühere Indexformat; im Repository
existiert dafür kein eigener `v0.2`-Tag. Eine manuelle Rückkehr benötigt daher
einen ausdrücklich bekannten kompatiblen Programmstand und den archivierten
`index.db`; die Kundendatenbank ist davon nicht betroffen.

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

Unter Linux ist `./start.sh` der gemeinsame Einstiegspunkt. Es startet den
Docker-Indexserver und die eigenständige Indexserver-Tray-Anwendung im
Hintergrund, synchronisiert bestmöglich die neueste Generation und öffnet die
GUI. Wird das Hauptfenster beendet, bleibt die Indexserver-Steuerung im
Systemtray verfügbar. Ohne erreichbaren Server verwendet PapaGUI den letzten
lokal geprüften Stand. Die GUI baut selbst keinen Index mehr auf.

Die Indexserver-Konsole lässt sich unabhängig vom Hauptfenster mit
`./index-tray.sh` starten. `./index-tray.sh --background` öffnet zunächst nur das
Systemtray-Icon; ein weiterer Aufruf aktiviert die bereits laufende Konsole.

Werkzeuge:

- Release-Builds enthalten Poppler (`pdftotext` und `pdftoppm`) für Windows x64,
  macOS ARM64/x64 und Linux x64. Ein Quellstart nutzt alternativ eine vorhandene
  Poppler-Installation und fällt für PDF-Text auf pypdf zurück.
- Tesseract bleibt für OCR erforderlich; ohne Tesseract funktioniert die
  normale PDF-Texterkennung weiterhin.
- LibreOffice sowie `catdoc` oder `antiword` für alte Office-Dateien

Fehlende optionale Programme verhindern den normalen Start nicht.

Für reproduzierbare Builds steht zusätzlich `requirements-lock.txt` mit exakt gepinnten Versionen bereit.

## Headless-Indexdienst mit Docker

Katalogaufbau, Kundenerkennung und Dokumentinhaltssuche laufen ausschließlich im
Docker-Container. Für den normalen lokalen Test genügt `./start.sh`. Details zu
Generationsexport, API, Konflikten, Backups und Synology stehen in
[DOCKER.md](DOCKER.md). Ein manueller Serverlauf ist weiterhin möglich:

```bash
mkdir -p docker-data docker-config
export PAPAGUI_SOURCE_PATH=/absoluter/pfad/zu/den/Bauvorhaben
export PAPAGUI_UID=$(id -u)
export PAPAGUI_GID=$(id -g)
docker compose build
docker compose run --rm indexer --source /source --data /data --once
```


## Tests

```bash
.venv/bin/pip install -r requirements-test.txt
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/run_ci.py
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/run_ci.py --coverage
```

Die Tests prüfen unter anderem Katalogaktivierung, Queue-Fortsetzung,
Jahres-Shard-Rollover, progressive Mehr-Shard-Suche, inkrementelle Indexierung,
Ressourcenprofile, parallele Extraktion, Timeout-Reparatur, PDF-Fallback,
stufenweise OCR, Diagnosewerte, Dateisystemänderungen, Viewer und Kunden-CRUD.

Die Suite ist unter `tests/` nach `unit`, `integration`, `ui`, `e2e` und
`platform` gegliedert. Wiederverwendbare Basisklassen, Fixture-Builder und
Test-Doubles liegen getrennt unter `base`, `builders` und `fakes`. Der Runner
startet jedes Testmodul in einem eigenen Prozess, damit Qt zuverlässig beendet
wird. Mit `--coverage` kombiniert er deren Messdaten und erzwingt für den
Produktionscode 100 Prozent Zeilen- und Zweigabdeckung. Dieselbe Prüfung läuft
in GitHub Actions separat unter Linux, macOS und Windows.

## Kundenvorschläge und Kundenübersicht

Die Anwendung unterstützt eine Kundenverwaltung, die von einzelnen Dienstleistungen getrennt ist. Ein Kunde kann mehrere Dienstleistungen und mehrere verknüpfte Projektordner haben.

- Kundentypen sind dynamisch und können beim Bearbeiten frei gepflegt werden (z. B. Privatkunde, Firma, Gemeinde).
- Kundendaten können über Vorschläge aus Ordnerstruktur und Dokumentinhalten vorbereitet und anschließend im Dialog bestätigt werden.
- Die automatische Erkennung ist standardmäßig deaktiviert und muss unter „Kundenerkennung“ ausdrücklich eingeschaltet werden.
- Projektordner mit demselben normalisierten Kundennamen werden automatisch einem gemeinsamen Kunden zugeordnet. Bereits vorhandene Namensdubletten werden beim Öffnen der Kundendatenbank verlustfrei zusammengeführt und intern protokolliert.
- Nur ähnliche Namen oder widersprüchliche bestehende Ordnerzuordnungen werden dauerhaft zur manuellen Prüfung vorgemerkt.
- Unterhalb des Hauptbereichs gibt es eine Kundenübersicht mit Kundenliste (links) und dynamischer Detailansicht (rechts).

Die Übersicht ist Teil der normalen Seitenansicht und über vertikales Scrollen erreichbar.
