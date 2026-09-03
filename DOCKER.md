# Docker-Indexserver

Der Dockercontainer ist die einzige schreibende Instanz für Index,
Kundenerkennung und `customers.db`. Der Desktop-Client ist davon als eigenes
Produkt getrennt.

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
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml up -d --build
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml logs -f papagui-server
```

Beenden funktioniert auch dann, wenn die Quelldisk gerade nicht eingehängt ist:

```bash
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml down
```

Das vollständige Betriebs-, Backup-, Restore- und Synology-Runbook steht unter
[docs/server/operations.md](docs/server/operations.md).
