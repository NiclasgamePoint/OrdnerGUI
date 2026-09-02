# Headless-Indexdienst mit Docker

Der Container ist die einzige schreibende Instanz für Katalogaufbau,
Kundenerkennung und Dokumentinhaltsindexierung. Die Quelldaten werden
ausschließlich lesend unter `/source` eingebunden. Der Server veröffentlicht nach
jedem erfolgreichen Lauf eine unveränderliche, mit SHA-256 geprüfte Generation
aus Index und `customers.db`.

## Einfacher Start zusammen mit PapaGUI

Unter Linux genügt:

```bash
./start.sh
```

Das Skript liest die bereits in PapaGUI gewählte Datenquelle, erzeugt einmalig ein
lokales API-Token, baut beziehungsweise aktualisiert das Image und startet den
Indexdienst per Docker Compose sowie die eigenständige Indexserver-Steuerung im
Hintergrund. Sie bleibt auch nach dem Schließen des Hauptfensters im Systemtray
verfügbar. Serverdaten liegen getrennt in `docker-server-data`, der verifizierte
Clientcache in `client-data`. Die GUI baut niemals selbst einen Index. Der
Build- und Startlog liegt unter
`docker-server-data/logs/docker-indexer-startup.log`; Laufzeitlogs zeigt
weiterhin
`docker compose logs -f indexer`.

Beim Start und danach im unter Einstellungen festgelegten Intervall lädt der
Client `current.json` und nur bei einer neuen Generation das ZIP-Archiv. Größe,
Archiv-Prüfsumme und jede einzelne Datei werden geprüft. Erst danach wird der
symbolische `current`-Verweis atomar umgeschaltet. Ist der Server nicht
erreichbar, bleibt die letzte gültige lokale Generation ohne Einschränkung für
Suche und Anzeige aktiv.

Server und Client behalten jeweils den aktiven Stand plus genau drei vorherige
vollständige Generationen. Eine Generation enthält `customers.db`, den Katalog,
den Inhaltsstatus und sämtliche Inhaltsshards.

## Kunden-API und Konflikte

`customers.db` auf dem Server ist die Single Source of Truth. Kunden- und
Journaländerungen verwenden eine Bearbeitungsrevision. Ein Client sendet immer
die Revision, die er gelesen hat. Ist sie inzwischen veraltet, antwortet die API
mit `409 Conflict` und dem aktuellen Datensatz; fremde Änderungen werden niemals
stillschweigend überschrieben. Offlineänderungen landen in
`client-data/customer-offline-queue.db` und werden später mit derselben Prüfung
übertragen.

Die API läuft standardmäßig auf Port `8765`. `start.sh` verwaltet für lokale
Tests das Token in `docker-config/api-token`. Auf der Synology muss
`PAPAGUI_API_TOKEN` als Secret gesetzt und der Zugriff zusätzlich über HTTPS
(Reverse Proxy) abgesichert werden.

Jede angenommene Kundenänderung veröffentlicht sofort wieder eine vollständige
Generation. So erhalten andere Clients nicht erst beim nächsten nächtlichen
Indexlauf den neuen Stand. Fehlt lokal das Docker-Buildx-Plugin, fällt
`start.sh` automatisch auf den klassischen Docker-Builder zurück.

## Steuerung über das Systemtray

`./start.sh` startet das separate Indexserver-Tray automatisch. Der Eintrag
**Indexserver** im PapaGUI-Systemtray aktiviert dieselbe zentrale
Bedienoberfläche für den Docker-Dienst. Die Oberfläche ist nicht an das
PapaGUI-Hauptfenster gebunden und kann unter Linux auch eigenständig gestartet
werden:

```bash
./index-tray.sh
```

Mit `./index-tray.sh --background` startet sie zunächst nur als Tray-Icon. Ein
weiterer Aufruf öffnet die bereits laufende Instanz, statt ein zweites Tray zu
erzeugen. Das Hauptprogramm muss dafür nicht laufen; benötigt werden nur eine
erreichbare Server-API und das API-Token in `docker-config/api-token`.

Die Konsole zeigt den Live-Zustand des Servers,
die aktuelle Katalog-/Inhaltsphase, Worker, Fortschritt, Generation und die drei
Serverbackups. Dort lassen sich ein inkrementeller Lauf, ein vollständiger
Neuaufbau, Abbruch, Löschen mit anschließendem Neuaufbau und ein kontrollierter
Containerneustart auslösen.

OCR, Ressourcenprofil, Dateiformate, Größenlimits, Ausschlüsse, Priorisierung und
das automatische Laufintervall werden ebenfalls dort serverseitig gespeichert.
Das Laufintervall besteht aus Zahlenfeld und Einheit (Minuten oder Stunden), ist
auf 15 Minuten bis 48 Stunden begrenzt und bleibt in
`docker-server-data/index-server-settings.json` über Containerneustarts erhalten.
Die Hauptanwendung enthält im Docker-Modus nur noch echte Clientoptionen wie
Suchumfang und Abrufintervall. Die Steuerbefehle laufen authentifiziert über die
Server-API und funktionieren daher später unverändert gegen die Synology. Für den
Containerneustart beendet sich der Dienst kontrolliert; die Compose-Richtlinie
`restart: unless-stopped` startet ihn anschließend erneut.

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

Die Synology stellt Port `8765` beziehungsweise den HTTPS-Reverse-Proxy für die
Clients bereit. `PAPAGUI_INDEX_SERVER_URL` zeigt auf diese Adresse. Quelldaten
bleiben read-only; `docker-server-data` und `docker-config` benötigen
Schreibrechte für die Container-UID/GID.

## Einschränkungen des Prototyps

- Eine Containerinstanz pro Server-Ausgabeverzeichnis.
- Noch keine grafische Konfliktzusammenführung Feld für Feld; Konflikte werden
  sicher abgewiesen und zur Entscheidung behalten.
- Die API ist bewusst klein und besitzt noch keine Administrations-Weboberfläche.
- Der Compose-Healthcheck bestätigt den gestarteten Dienststatus, nicht die
  fachliche Vollständigkeit des letzten Indexlaufs. Details stehen in
  `index/jobs/service/index_job.json` und den Logs.
