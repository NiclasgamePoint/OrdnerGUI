# Docker-Indexserver

Der Dockercontainer ist die einzige schreibende Instanz für Index,
Kundenerkennung und `customers.db`. Pro Datenvolume darf nur eine
Serverinstanz laufen. Der Desktop-Client ist davon als eigenes Produkt
getrennt. Das Image enthält keine Qt- oder Clientabhängigkeit.

Für den lokalen Komplettstart:

```bash
# Linux
./start.sh

# macOS
./start.command
```

```powershell
# Windows
.\start.ps1
# oder: start.bat
```

Server allein:

```bash
cp deploy/server/.env.example deploy/server/.env
# absolute Quell-, Daten- und Configpfade in .env eintragen
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml build
# api-token geschützt im Configpfad erzeugen; ein Adminpasswort ist nicht nötig;
# genaue Befehle: docs/server/operations.md
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml up -d
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml logs -f papagui-server
```

Beenden funktioniert auch dann, wenn die Quelldisk gerade nicht eingehängt ist:

```bash
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml down
```

Die Quelle wird schreibgeschützt unter `/source` eingebunden. `/data` enthält
Index, Kundendaten, privaten Extraktionscache, dauerhafte Aufträge und
Generationen; `/config` enthält Einstellungen, Quellidentität und
`api-token`. Port 8765 wird standardmäßig nur an `127.0.0.1` gebunden.
Der tokenfreie Healthcheck bestätigt die Erreichbarkeit, während
`/v2/server/status` den fachlichen Zustand und Dokumentworker meldet.

Servereinstellungen und Wartung sind mit dem Client-Token über den Client
oder die v2-API erreichbar. Ressourcenprofile bestimmen anhand verfügbarer
CPU-/RAM-Kapazität und vorhandener Containergrenzen 1–20 Dokumentworker.
Die Compose-Vorlage setzt keine festen CPU-/RAM-Limits. Unveränderte
Dokumente werden im regulären Lauf übernommen; der aktivierte tägliche
Inhaltsabgleich und ein Indexneuaufbau prüfen Hashes und dürfen passende
Parser-/OCR-Ergebnisse wiederverwenden. Nur gezielte Ausleseaufträge und
der vollständige Erkennungsneuaufbau erzwingen die erneute Auslesung.

Sperrlistenänderungen werden gesammelt bestätigt und veröffentlichen
einmal den Kundenstand. Sie starten keine Dokumentauslesung. Ist nach
erfolgreichem Speichern nur die Veröffentlichung offen, kann diese im
Client wiederholt werden; der offene Zustand bleibt über Neustarts erhalten.

Das vollständige Betriebs-, Backup-, Restore- und Synology-Runbook steht unter
[docs/server/operations.md](docs/server/operations.md).
