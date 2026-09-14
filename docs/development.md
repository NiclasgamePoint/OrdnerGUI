# Entwicklung

## Einrichtung

Vom Repositoryverzeichnis aus mit Python 3.11 arbeiten; diese Version verwenden
auch die CI-Definitionen. Jede Plattform benötigt ihre eigene virtuelle Umgebung.
Eine Linux-`.venv` lässt sich nicht auf Windows weiterverwenden.

Linux/macOS:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r packages/server/requirements-lock.txt -r packages/client/requirements-lock.txt -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps -e packages/contracts -e packages/server -e packages/client
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packages/server/requirements-lock.txt -r packages/client/requirements-lock.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e packages/contracts -e packages/server -e packages/client
```

Der Client-Lock enthält PySide6 für die GUI. Eine Aktivierung der Umgebung ist
bei diesen direkten Interpreteraufrufen nicht nötig.
Graphify ist ein separates Entwicklungswerkzeug und gehört nicht in Client-
oder Server-Runtime-Abhängigkeiten; sein erlaubter Quellumfang und die Grenzen
des Fingerprint-Helfers stehen im [Graphify-Runbook](development/graphify.md).

## Tests und Qualität

Der [lokale Windows-Teststand vom 11. September 2026](development/windows-baseline-2026-09-11.md)
dokumentiert die Werkzeugprüfung und offene Fehler des Gesamtlaufs.

```bash
.venv/bin/python -m ruff check packages tests tools main.py
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/run_ci.py --coverage
.venv/bin/python -m pytest tests/system/test_wire_interop.py -q
bash -n tools/docker_smoke.sh
bash tools/docker_smoke.sh
.venv/bin/python -m pip install -r packaging/requirements-build-lock.txt
.venv/bin/python -m build --no-isolation packages/contracts
.venv/bin/python -m build --no-isolation packages/server
.venv/bin/python -m build --no-isolation packages/client
```

Unter Windows stehen die Python-Prüfungen ebenfalls zur Verfügung:

```powershell
.\.venv\Scripts\python.exe -m ruff check packages tests tools main.py
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe tests/run_ci.py --coverage
.\.venv\Scripts\python.exe -m pytest tests/tools/test_start_scripts.py -q
Remove-Item Env:QT_QPA_PLATFORM
```

Der Testtreiber isoliert jedes Testmodul in einem Prozess mit temporären
Datenverzeichnissen; das Standardzeitlimit beträgt 300 Sekunden je Modul
(`--module-timeout`). `.coveragerc` verlangt 93 Prozent Gesamtdeckung unter
Einbeziehung von Branch Coverage. Docker-Smokes benötigen Bash und eine
laufende Docker-Engine; ihr CI-Job läuft unter Linux.

Coverage-Zwischenstände mit Rechnername und Prozess-ID (`.coverage.<Rechner>.<PID>.*`)
entstehen während der einzelnen Testprozesse. Der Treiber sammelt sie in einem
temporären Verzeichnis und führt sie auch bei Testfehlern oder Strg+C in die
einzelne Datei `.coverage` zusammen. Das temporäre Verzeichnis wird anschließend
entfernt, auch wenn das Zusammenführen fehlschlägt. Ein fehlgeschlagener Lauf
bleibt fehlgeschlagen; seine Teilmessung gilt nicht als bestandene Coverage-Prüfung.
Ein hartes Beenden des Betriebssystemprozesses kann temporäre Dateien im
System-Tempverzeichnis hinterlassen, aber keine Zwischenstände im Projektordner.
Alte Zwischenstände im Projektordner können einmalig mit
`python -m coverage combine` zusammengeführt werden. `.coveragerc` ist die
Konfiguration und bleibt erhalten.

Architekturtests prüfen verbotene Imports und den Inhalt gebauter Artefakte.
Der Wire-Interop-Test startet einen echten Serverprozess auf einem freien
Localhost-Port und verwendet ausschließlich die produktiven HTTP-, Sync- und
SQLite-Clientadapter. Er deckt Generationstransfer, portable Suche,
Kunden-Idempotenz und -Konflikte sowie Admin-Einstellungen ab. Der Docker-Smoke
prüft zusätzlich den kontrollierten Exitcode 75 mit Restart-Policy, persistente
Generationen/Einstellungen und einen Restore auf Wegwerfkopien. Server-Smokes
müssen ohne installiertes PySide6 laufen. `quality.yml` definiert Clienttests
für Linux, Windows und macOS sowie Linux-Jobs für Server, Wire-Interop und
Repository-Coverage. Der manuell auslösbare Workflow `client-artifacts.yml`
definiert native Builds für Linux x64, Windows x64 sowie macOS x64/ARM64.
Diese Definitionen belegen keine erfolgreiche Ausführung auf einem Zielsystem;
dafür sind die Ergebnisse des jeweiligen CI-Laufs beziehungsweise lokale
Prüfungen auf dieser Plattform maßgeblich.

## GUI-Tests mit pytest-qt

`requirements-test.txt` enthält die Qt-freien Testwerkzeuge für Server und
Contracts. `requirements-client-test.txt` ergänzt pytest-qt 4.5.0 für Clienttests;
`requirements-dev.txt` ergänzt Pyinstrument 5.1.3 für die lokale Entwicklung.
Die Client- und Gesamtprüfungen in CI installieren die Client-Testabhängigkeiten,
die Server- und Wire-Jobs bleiben ohne pytest-qt und ohne Qt.

Die Layoutregressionen in `tests/client/test_customer_suggestions_layout.py`
verwenden `qapp` für die Anwendung, `qtbot.addWidget` für das Aufräumen der Dialoge
und `qtbot.waitSignal` für die Prüfung des Signals beim Öffnen einer Quelle.
Neue GUI-Regressionen können dieselben Fixtures verwenden. Testdaten bleiben
synthetisch; für vollständige Prüfungen isoliert `tests/run_ci.py` jedes Modul.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/client/test_customer_suggestions_layout.py -q
```

## Laufzeiten mit Pyinstrument untersuchen

Ein reproduzierbarer Einstieg ist der vorhandene synthetische
Kundenerkennungsbenchmark. Der Profiling-Aufruf führt ihn standardmäßig dreimal
aus und schreibt einen lokalen interaktiven HTML-Bericht:

```powershell
.\.venv\Scripts\python.exe tools/profile_recognition.py --repeat 3
```

Unter Linux/macOS denselben Aufruf mit `.venv/bin/python` verwenden.
Die Ausgabe liegt unter `profiles/recognition.html`; `--output` erlaubt einen
anderen Pfad. `profiles/` ist Git-ignoriert. Der Benchmark liest ausschließlich
die synthetische Fixture und öffnet weder Kundendokumente noch produktive
Einstellungen. Er misst die fachliche Erkennung, nicht OCR oder GUI-Latenzen.
Pyinstrument ist eine Entwicklungsabhängigkeit und gehört nicht in Produktartefakte.

Den Katalogaufbau einschließlich Metadaten, Textspeicherung, Cache und Wiederholung
misst ein separater Benchmark mit 1.500 frisch erzeugten Textdateien:

```powershell
.\.venv\Scripts\python.exe tools/profile_catalog.py --documents 1500
```

Er schreibt Messwerte nach `profiles/catalog.json` sowie je Lauf einen HTML- und
Textbericht. Der synthetische Leser arbeitet ohne OCR und externe Parser, damit
die Kosten des Katalogs sichtbar werden. Gemessen werden Metadaten allein und
der Katalog mit Textspeicherung, jeweils beim ersten und unveränderten Folgelauf.
Die temporäre Quelle und die Testdatenbanken werden anschließend entfernt.
Ergebnisse und Grenzen des Vorher/Nachher-Vergleichs stehen im
[Katalog-Prüfbericht](development/catalog-performance-2026-09-11.md).

Für einen einzelnen GUI-Test lässt sich die CLI direkt nutzen, nachdem `profiles/`
angelegt wurde:

```powershell
.\.venv\Scripts\python.exe -m pyinstrument -r html -o profiles/gui-test.html -m pytest tests/client/test_customer_suggestions_layout.py -q
```

Der Profiler misst hier den gestarteten Testprozess; Arbeit in separaten
Serverprozessen oder Workerthreads muss gesondert profiliert werden.

## Lokale Prozesse

- `papagui-server`: headless API, Scheduler und Indexjobs
- `papagui-client`: Desktop-Hauptfenster
- `papagui-tray`: eigenständige Serversteuerung
- `start.sh`: Linux-Komfortstart für Container, Tray und Client
- `start.command`: macOS-Wrapper für denselben kontrollierten Start
- `start.ps1` beziehungsweise `start.bat`: Windows-Komfortstart

Die POSIX-Skripte werden mit `bash -n` geprüft. Der Windows-CI-Job sieht eine
PowerShell-AST-Prüfung von `start.ps1` vor. Die Windows-Tests führen dessen
Secret-Hilfsfunktionen mit synthetischen temporären Dateien aus: Token-Erzeugung,
Speicherung, Wiederverwendung, Dateirechte und Generatorfehler. Der Serverstart
wird mit einem lokalen Docker-Ersatz und simulierten Healthchecks geprüft,
einschließlich Buildfehlern, Containerabbruch und Offline-Start. Dabei werden
keine echten Container oder GUI-Prozesse gestartet. Auf anderen Plattformen
werden diese Fälle übersprungen.
`start.bat` delegiert an Windows PowerShell. Ein lokal erzeugtes natives
Artefakt wird mit `python tools/check_frozen_client.py dist` auf Paketgrenzen
geprüft und über `--help` beziehungsweise einen Offline-Sync-Smoke gestartet.
Auf macOS liegen `PapaGUI Client.app` und `PapaGUI Tray.app` als zwei
eigenständige Prozesse nebeneinander. Der Client startet den Tray aus dem
benachbarten Bundle; deshalb müssen beide Apps gemeinsam installiert oder
verschoben werden. Es wird keine zweite Tray-Kopie in das Client-Bundle gelegt.

Ein Produktionsclient darf den Server nicht als Unterprozess starten. Diese
Kopplung existiert nur im lokalen Entwicklungsskript.

Für den Komplettstart werden die eingerichtete Umgebung und Docker Compose
benötigt; unter Windows Docker Desktop mit Linux-Containern starten:

```bash
# Linux
./start.sh
# macOS / Finder
./start.command
```

```powershell
# Windows PowerShell; Beispielpfad durch die vorhandene Datenquelle ersetzen
$env:PAPAGUI_SOURCE_PATH = 'C:\Daten\Bauvorhaben'
.\start.bat
```

Für die konfigurierte lokale Serververbindung lässt `start.bat` über `start.ps1`
automatisch das Server-Image bauen und einen
fehlenden Container erstellen; vorhandene Container werden gestartet oder bei
Änderungen aktualisiert. Docker verwendet beim Build seinen Cache. Build- und
Startausgaben bleiben im Konsolenfenster sichtbar. Erst nach erfolgreichem
Compose-Aufruf wartet der Starter auf die Server-API und öffnet danach den Client
mit `pythonw.exe`. Der Client startet selbst den unabhängigen Indextray, ebenfalls
ohne Konsole. Das Startskript beendet sich danach; beim Doppelklick auf `start.bat`
schließt damit auch das Konsolenfenster. Für `--sync-only` und `--help` bleibt die
Konsolenausgabe erhalten. Bei Docker-Fehlern nennt der Starter die Ursache und
startet den Client offline.

Die erzeugten Client-Standardwerte werden beim ersten Start in der geschützten
`client-config.json` gespeichert. Sie bleiben in der Oberfläche bearbeitbar.
Gespeicherte Einstellungen haben Vorrang vor diesen Starter-Standardwerten;
explizit gesetzte Umgebungsvariablen behalten dagegen ihren Vorrang.
Mit gespeicherter NAS-Adresse startet der Windows-Starter nur den Client samt
Tray und ändert die lokale Server-Tokendatei nicht.

Beim ersten Serverstart kann die API schon erreichbar sein, während der erste
Index noch aufgebaut wird. Der Client zeigt dann „Server erreichbar · Noch kein
fertiger Index verfügbar.“ und prüft bei aktiver automatischer Synchronisierung
alle fünf Sekunden erneut. Vorhandene lokale Daten bleiben dabei erhalten.
Sobald eine vollständige Generation bereitsteht, wird sie übernommen und das
normale Synchronisierungsintervall gilt wieder.

Der Indextray verwendet eine benutzerbezogene Dateisperre zusätzlich zum lokalen
Qt-Socket. Weitere Statusklicks aktivieren das vorhandene Fenster; sie erzeugen
keine weiteren Tray-Fenster. Nach einem Update der Tray-Steuerung alte
Indextray-Instanzen einmal über deren Menü beenden und den Client neu starten.

Die Starter verwenden standardmäßig `Bauvorhaben` im Repository. Ein anderer
Mount wird über `PAPAGUI_SOURCE_PATH` gesetzt. Dieses Verzeichnis ist nicht in
Git enthalten. Fehlt es, meldet der Windows-Starter den fehlenden Quellordner
und startet keinen Servercontainer;
eine geöffnete GUI kann dann leer oder offline sein. Der lokale Serverstart
kann die konfigurierte Quelle indexieren. Für einen Entwicklungs-Smoke nur
einen eigens angelegten synthetischen Quellordner verwenden.

Mit `PAPAGUI_SKIP_DOCKER=1` startet nur der Client samt Tray; vorhandene lokale
Generationen bleiben offline nutzbar. In PowerShell lautet die Zuweisung
`$env:PAPAGUI_SKIP_DOCKER = '1'`. Für Tests und Offline-Smokes keine echten
Kundendaten, Datenbanken, Logs oder produktiven Einstellungen als Testeingaben
verwenden. Native Artefakte werden separat gemäß
[Packaging-Anleitung](../packaging/client/README.md) gebaut.

## Änderungsreihenfolge

Der vollständige Windows-Releasecheck ist mit
`powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_release_checks.ps1`
ausführbar. Er benötigt eine Python-3.11-Umgebung; weitere Details und die Grenzen
lokaler CI-Ausführung stehen in [Releasevorbereitung](releasing.md).

Vertragsänderungen beginnen in `contracts`, erhalten Tests und werden danach in
Server und Client implementiert. Wireformat- oder Datenbankschemaänderungen
benötigen Kompatibilitätsfixtures und einen dokumentierten Migrationspfad.
