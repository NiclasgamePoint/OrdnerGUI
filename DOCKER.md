# Headless-Indexdienst mit Docker

Der Container führt Katalogaufbau, Kundenerkennung und Dokumentinhaltsindexierung
ohne gestartete PapaGUI-Oberfläche aus. Die Quelldaten werden ausschließlich
lesend unter `/source` eingebunden. Katalog, Inhaltsshards, Jobstatus,
`customers.db` und Logs liegen persistent unter `/data`.

## Lokaler Probelauf

Zuerst lokale Ausgabe- und Konfigurationsverzeichnisse mit der UID/GID des
ausführenden Benutzers anlegen:

```bash
mkdir -p docker-data docker-config
export PAPAGUI_SOURCE_PATH=/absoluter/pfad/zu/den/Bauvorhaben
export PAPAGUI_UID=$(id -u)
export PAPAGUI_GID=$(id -g)
docker compose build
docker compose run --rm indexer --source /source --data /data --once
```

Der einmalige Lauf eignet sich für den ersten Test. Der dauerhafte Dienst startet
sofort einen Lauf und danach standardmäßig alle 24 Stunden einen weiteren:

```bash
docker compose up -d indexer
docker compose logs -f indexer
```

Das Intervall lässt sich beispielsweise auf eine Stunde setzen:

```bash
export PAPAGUI_INDEX_INTERVAL_SECONDS=3600
docker compose up -d indexer
```

Ein vollständiger Neuaufbau kann ohne Änderung des dauerhaften Dienstes gestartet
werden:

```bash
docker compose run --rm indexer \
  --source /source --data /data --once --full-rebuild
```

## Persistente Daten und Status

Wichtige Ausgaben im Datenmount:

```text
data/
├── customers.db
├── index/
│   ├── catalog/active.db
│   ├── catalog/backups/
│   ├── content/state.db
│   ├── content/shards/
│   └── jobs/service/index_job.json
└── logs/
```

Der Service reagiert auf `SIGTERM`/`SIGINT` und beendet sich zwischen den
persistenten Phasen. Queue und Katalogaufbau sind fortsetzbar beziehungsweise
rekonstruierbar. Während eines Schreibvorgangs darf kein zweiter Container mit
demselben `/data`-Mount gestartet werden.

## Synology-Vorbereitung

Für einen späteren Synology-Betrieb müssen Quell- und Ausgabeverzeichnis als
absolute NAS-Pfade in Container Manager eingetragen werden. Die konfigurierte
UID/GID benötigt Leserechte auf der Quelle und Schreibrechte auf Ausgabe und
Konfiguration. Die Quelle sollte weiterhin read-only gemountet bleiben.

Dieses erste Docker-Inkrement erzeugt den vollständigen zentralen Index, verteilt
ihn aber noch nicht automatisch an Clients. Für die spätere Client-Synchronisation
sollte niemals ein gerade beschriebener SQLite-Bestand kopiert werden. Vorgesehen
ist stattdessen eine veröffentlichte, unveränderliche Generation mit Manifest und
Prüfsummen, die der Client vollständig in ein temporäres Verzeichnis lädt und
anschließend atomar aktiviert. `customers.db` braucht dabei eine eigene
Konfliktstrategie, sobald Clients Kundendaten bearbeiten dürfen.

## Einschränkungen des Prototyps

- Eine Containerinstanz pro Ausgabeverzeichnis.
- Noch kein Generationsexport und kein automatischer Client-Download.
- Einstellungen werden über das persistente QSettings-Verzeichnis `/config`
  übernommen; eine eigene Weboberfläche oder API existiert noch nicht.
- Der Compose-Healthcheck bestätigt den gestarteten Dienststatus, nicht die
  fachliche Vollständigkeit des letzten Indexlaufs. Details stehen in
  `index/jobs/service/index_job.json` und den Logs.
