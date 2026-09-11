# PapaGUI

PapaGUI besteht ab Version 0.4.2 aus einem nativen Desktop-Client und einem
unabhängigen, headless betriebenen Indexserver. Der Server ist die einzige
schreibende Instanz für Index, Kundenerkennung und `customers.db`. Clients
halten ausschließlich geprüfte lokale Kopien und bleiben mit dem letzten
gültigen Stand offline benutzbar.

Der Stand 0.4.2 ist ein Entwicklungs-Checkpoint. Die enthaltenen Workflows bauen
Testartefakte ohne Signierung und ohne Registry-Push; sie veröffentlichen keine
GitHub Releases. Der tatsächliche Status eines Builds ist im jeweiligen
Workflow-Lauf zu prüfen.

## Komponenten

```text
packages/
├── contracts/    Gemeinsame API-, Manifest- und Kompatibilitätsverträge
├── server/       Indexaufbau, Kundenerkennung, Kunden-API und Scheduler
└── client/       Desktop-GUI, Tray, Synchronisation und Offline-Outbox

deploy/server/    Docker Compose und Synology-Betrieb
packaging/client/ Vorbereitete native Client-Builds
```

Die Importgrenzen werden automatisch geprüft: Contracts kennen keine
Infrastruktur, der Server kennt weder Client noch Qt und der Client enthält
keinen Index-Writer oder Servercode.

## Funktionsweise

1. Der Server liest die gemountete Datenquelle und baut daraus unveränderliche
   Indexgenerationen.
2. `customers.db` bleibt auf dem Server die Single Source of Truth.
3. Clients prüfen beim Start und regelmäßig das Generationsmanifest, laden nur
   geänderte Komponenten und aktivieren sie atomar.
4. Bei einem Serverausfall verwendet der Client den letzten gültigen lokalen
   Stand. Kundenänderungen werden in einer separaten Offline-Outbox gehalten.
5. Gleichzeitige Änderungen verwenden Revisionen: Die erste Änderung gewinnt,
   weitere Clients erhalten `409 Conflict` und müssen neu laden oder bewusst
   zusammenführen.

Server und Client behalten jeweils den aktiven Stand plus drei vorherige
gültige Generationen. Ein fehlgeschlagener Build oder Download verdrängt keine
funktionierende Sicherung.

## Entwicklung starten

Python 3.11 oder neuer wird benötigt; die CI verwendet Python 3.11. Zuerst die
festgelegten Abhängigkeiten, anschließend die drei Pakete editierbar installieren:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r packages/server/requirements-lock.txt -r packages/client/requirements-lock.txt -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps -e packages/contracts -e packages/server -e packages/client
```

Unter Windows befindet sich Python unter `.venv\Scripts\python.exe`; unter
macOS und Linux unter `.venv/bin/python`.

### Wechsel auf einen Windows-Entwicklungsrechner

Nach dem Klonen beziehungsweise Aktualisieren des Repositorys eine **neue**
virtuelle Umgebung anlegen; eine Linux- oder macOS-Umgebung lässt sich nicht
übernehmen. Python 3.11 wird auch in den Plattformtests verwendet. Im
Projektverzeichnis in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packages\server\requirements-lock.txt -r packages\client\requirements-lock.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e packages\contracts -e packages\server -e packages\client
```

Für **Client und Server auf dem Windows-PC** Docker Desktop mit Linux-Containern
starten. Vor dem Komplettstart einen vorhandenen, absoluten Quellordner angeben:

```powershell
$env:PAPAGUI_SOURCE_PATH = 'D:\Projektdaten' # Beispiel: durch den eigenen Ordner ersetzen
.\start.bat
```

Ohne diese Angabe erwartet das Startskript einen Ordner `Bauvorhaben` im
Repository. Fehlt der Quellordner, wird kein Server gestartet; der Client kann
trotzdem im Offlinebetrieb öffnen.

Wenn **der bisherige Indexserver weiterläuft**, nur den Client starten:

```powershell
.\.venv\Scripts\python.exe -m papagui_client
```

In den Clienteinstellungen die vom Windows-PC erreichbare Server-URL, den
bestehenden Client-API-Token und die Windows-Pfadzuordnung zur Datenquelle
eintragen. `127.0.0.1` verweist auf den Windows-PC selbst. Docker ist für diesen
Clientbetrieb nicht erforderlich.

Git überträgt den Programmcode, aber keine Dokumente, lokalen Einstellungen,
Tokens oder Datenbanken. Insbesondere bleiben Sperrliste und bisherige
Kundenentscheidungen im Datenvolume des bisherigen Servers. Soll auch der Server
umziehen, dessen Daten- und Konfigurationsverzeichnisse separat gemäß
[Betriebsanleitung](docs/server/operations.md) sichern und übernehmen. Einen
bestehenden Server nur wegen des Clientwechsels neu aufzubauen ist nicht nötig.

### Lokaler Komplettstart

Der lokale Komplettstart ist auf allen drei Entwicklungsplattformen vorbereitet.
Docker Engine samt Compose-Plugin muss laufen; unter Windows und macOS genügt
Docker Desktop:

```bash
# Linux
./start.sh

# macOS (auch per Doppelklick im Finder)
./start.command
```

```powershell
# Windows PowerShell
.\start.ps1
# alternativ per Explorer/Eingabeaufforderung: start.bat
```

Die Skripte bauen und starten den Servercontainer im Hintergrund, warten auf
dessen Healthcheck und starten anschließend Client und eigenständiges Tray. Sie
erzeugen lokale Secretdateien mit eingeschränkten Rechten; nur deren Pfade, nie
die Secretwerte, werden an Docker übergeben. Der Produktionsclient selbst
benötigt weder Docker noch direkten Zugriff auf das Serverdatenvolume.

Einzelne Komponenten:

```bash
.venv/bin/python -m papagui_client
.venv/bin/python -m papagui_client.entrypoints.tray --background
.venv/bin/python -m papagui_server --help
```

## Tests

```bash
.venv/bin/python -m ruff check packages tests tools main.py
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/run_ci.py --coverage
```

Die Workflows sind für Linux, Windows und macOS vorbereitet. Server- und
Dockertests besitzen zusätzlich einen Qt-freien Job. Ob der jeweilige Commit die
Plattformtests bestanden hat, ist in den zugehörigen GitHub-Actions-Läufen zu
prüfen. Ein lokaler Linux-Testlauf bestätigt keinen nativen Windows-Start.
Buildartefakte bleiben nicht signierte CI-Artefakte und werden nicht
veröffentlicht.

## Dokumentation

- [Architektur](docs/architecture.md)
- [Client und Offlinebetrieb](docs/client.md)
- [Kundendatenerkennung, Sperrliste und Neuaufbau](docs/kundendatenerkennung.md)
- [Wiederhergestelltes v0.4.1-GUI-Design und Feature-Migration](docs/gui-design-restoration.md)
- [Serverbetrieb, Docker und Synology](docs/server/operations.md)
- [API v2](docs/api.md)
- [Datenformate und Migration](docs/data-and-migrations.md)
- [Entwicklung und Tests](docs/development.md)
- [Versionierung und spätere Releases](docs/releasing.md)
- [Sicherheitskonzept](SECURITY.md)
- [Graphify für die Architekturkarte](docs/development/graphify.md)
- [Umsetzungsstand und offene Arbeiten](Umsetzungsplanung.md)
- [Produktideen](Vorschlaege.md)

Historische Prüfungen und Entwurfsentscheidungen stehen getrennt von den
laufend gepflegten Anleitungen:

- [Prüfung der Kundendatenerkennung](docs/kundendatenerkennung-pruefbericht-2026-09-09.md)
- [Prüfung der parallelen Dokumentverarbeitung](docs/dokumentverarbeitung-pruefbericht-2026-09-09.md)
- [Ursprünglicher Umsetzungsplan mit aktuellem Status](docs/kundendatenerkennung-umsetzungsplan.md)

Die bisherige Docker-Einstiegsseite bleibt unter [DOCKER.md](DOCKER.md)
erreichbar.
