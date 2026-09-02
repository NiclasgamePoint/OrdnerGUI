# Entwicklung

## Einrichtung

```bash
python -m venv .venv
.venv/bin/pip install -e packages/contracts
.venv/bin/pip install -e packages/server
.venv/bin/pip install -e 'packages/client[gui]'
.venv/bin/pip install -r requirements-test.txt
```

Graphify ist ein separates Entwicklungswerkzeug und gehört nicht in Client-
oder Server-Runtime-Abhängigkeiten.

## Tests und Qualität

```bash
.venv/bin/ruff check packages tests tools
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/run_ci.py --coverage
.venv/bin/python -m pytest tests/system/test_wire_interop.py -q
bash -n tools/docker_smoke.sh
bash tools/docker_smoke.sh
.venv/bin/python -m build --no-isolation packages/contracts
.venv/bin/python -m build --no-isolation packages/server
.venv/bin/python -m build --no-isolation packages/client
.venv/bin/python tools/graphify_refresh.py --check
```

Architekturtests prüfen verbotene Imports und den Inhalt gebauter Artefakte.
Der Wire-Interop-Test startet einen echten Serverprozess auf einem freien
Localhost-Port und verwendet ausschließlich die produktiven HTTP-, Sync- und
SQLite-Clientadapter. Er deckt Generationstransfer, portable Suche,
Kunden-Idempotenz und -Konflikte sowie Admin-Einstellungen ab. Der Docker-Smoke
prüft zusätzlich den kontrollierten Exitcode 75 mit Restart-Policy, persistente
Generationen/Einstellungen und einen Restore auf Wegwerfkopien. Server-Smokes
müssen ohne installiertes PySide6 laufen. Die Workflows bereiten
plattformabhängige Clienttests beziehungsweise Builds für Linux x64, Windows
x64 sowie macOS x64/ARM64 vor. Vor dem ersten Push gelten diese Ziele nicht als
auf realen Runnern ausgeführt oder zertifiziert.

## Lokale Prozesse

- `papagui-server`: headless API, Scheduler und Indexjobs
- `papagui-client`: Desktop-Hauptfenster
- `papagui-tray`: eigenständige Serversteuerung
- `start.sh`: Linux-Komfortstart für Container, Tray und Client
- `start.command`: macOS-Wrapper für denselben kontrollierten Start
- `start.ps1` beziehungsweise `start.bat`: Windows-Komfortstart

Die POSIX-Skripte werden mit `bash -n` geprüft. Auf dem Windows-CI-Runner wird
`start.ps1` über den PowerShell-AST-Parser validiert; `start.bat` bleibt ein
kleiner, statisch getesteter Delegationswrapper. Ein lokal erzeugtes natives
Artefakt wird mit `python tools/check_frozen_client.py dist` auf Paketgrenzen
geprüft und über `--help` beziehungsweise einen Offline-Sync-Smoke gestartet.
Auf macOS liegen `PapaGUI Client.app` und `PapaGUI Tray.app` als zwei
eigenständige Prozesse nebeneinander. Der Client startet den Tray aus dem
benachbarten Bundle; deshalb müssen beide Apps gemeinsam installiert oder
verschoben werden. Es wird keine zweite Tray-Kopie in das Client-Bundle gelegt.

Ein Produktionsclient darf den Server nicht als Unterprozess starten. Diese
Kopplung existiert nur im lokalen Entwicklungsskript.

Für den Komplettstart werden Python 3.11, die editierbar installierten Pakete
und Docker Compose benötigt:

```bash
# Linux
./start.sh
# macOS / Finder
./start.command
```

```powershell
# Windows PowerShell oder Explorer
.\start.ps1
start.bat
```

Die Starter verwenden standardmäßig `Bauvorhaben` im Repository. Ein anderer
Mount wird über `PAPAGUI_SOURCE_PATH` gesetzt. Mit `PAPAGUI_SKIP_DOCKER=1`
startet nur der offlinefähige Clientpfad.

## Änderungsreihenfolge

Vertragsänderungen beginnen in `contracts`, erhalten Tests und werden danach in
Server und Client implementiert. Wireformat- oder Datenbankschemaänderungen
benötigen Kompatibilitätsfixtures und einen dokumentierten Migrationspfad.
