# Entwicklung

## Einrichtung

Vom Repositoryverzeichnis aus mit Python 3.11 arbeiten; diese Version verwenden
auch die CI-Definitionen. Jede Plattform benötigt ihre eigene virtuelle Umgebung.
Eine Linux-`.venv` lässt sich nicht auf Windows weiterverwenden.

Linux/macOS:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r packages/server/requirements-lock.txt -r packages/client/requirements-lock.txt -r requirements-test.txt
.venv/bin/python -m pip install --no-deps -e packages/contracts -e packages/server -e packages/client
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packages/server/requirements-lock.txt -r packages/client/requirements-lock.txt -r requirements-test.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e packages/contracts -e packages/server -e packages/client
```

Der Client-Lock enthält PySide6 für die GUI. Eine Aktivierung der Umgebung ist
bei diesen direkten Interpreteraufrufen nicht nötig.
Graphify ist ein separates Entwicklungswerkzeug und gehört nicht in Client-
oder Server-Runtime-Abhängigkeiten; sein erlaubter Quellumfang und die Grenzen
des Fingerprint-Helfers stehen im [Graphify-Runbook](development/graphify.md).

## Tests und Qualität

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

## Lokale Prozesse

- `papagui-server`: headless API, Scheduler und Indexjobs
- `papagui-client`: Desktop-Hauptfenster
- `papagui-tray`: eigenständige Serversteuerung
- `start.sh`: Linux-Komfortstart für Container, Tray und Client
- `start.command`: macOS-Wrapper für denselben kontrollierten Start
- `start.ps1` beziehungsweise `start.bat`: Windows-Komfortstart

Die POSIX-Skripte werden mit `bash -n` geprüft. Der Windows-CI-Job sieht eine
PowerShell-AST-Prüfung von `start.ps1` vor. Die Windows-Tests führen außerdem
ausschließlich dessen Secret-Hilfsfunktionen mit synthetischen temporären
Dateien aus: Token-Erzeugung, Speicherung, Wiederverwendung, Dateirechte und
Generatorfehler. Auf anderen Plattformen werden diese Fälle übersprungen.
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

Die Starter verwenden standardmäßig `Bauvorhaben` im Repository. Ein anderer
Mount wird über `PAPAGUI_SOURCE_PATH` gesetzt. Dieses Verzeichnis ist nicht in
Git enthalten. Fehlt es, startet der Windows-Starter keinen Servercontainer;
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

Vertragsänderungen beginnen in `contracts`, erhalten Tests und werden danach in
Server und Client implementiert. Wireformat- oder Datenbankschemaänderungen
benötigen Kompatibilitätsfixtures und einen dokumentierten Migrationspfad.
