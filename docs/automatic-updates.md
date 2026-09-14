# Automatische Programmupdates ab 0.5.0

Der Tag `0.4.3` ist der Kompatibilitätscheckpoint **PreRelease Testing**;
sein Quellstand blieb bei der notwendigen Historienbereinigung erhalten.
Die Updatefunktion gehört zum folgenden Entwicklungsstand 0.5.0.
Ein bereits installiertes 0.4.3 besitzt diesen Mechanismus noch nicht:
**Client und Tray müssen einmalig mit dem neuen Installer aktualisiert werden.**
Der Server benötigt einmalig den unten beschriebenen separaten Update-Dienst.
Quellcode-Starts, Entwicklerumgebungen und bewusst angehaltene Server aktualisieren sich nicht.

## Veröffentlichung auf GitHub

1. Paketversionen und Release Notes aktualisieren. Alle drei Pakete dieses
   gemeinsamen Releasezugs verwenden dieselbe Version.
2. Den geprüften Commit mit `0.5.0` beziehungsweise der nächsten numerischen
   Version taggen. Tags anschließend nicht verschieben.
3. Auf GitHub ein **stabiles Release** zu diesem Tag veröffentlichen.
   Entwürfe und als Pre-release markierte Veröffentlichungen werden nicht ausgerollt.
   Für Pre-releases gibt es einen separaten Windows-/Linux-Publisher: Er stellt
   manuell installierbare Installer und ein Serverimage bereit, ohne
   `papagui-update.json` zu erzeugen. Einzelheiten stehen in der
   [Beta-Arbeitsliste](beta-readiness.md).
4. `Publish verified release updates` führt Quality, die vier nativen Client-Builds
   und die Serverprüfungen aus. Erst danach werden das Serverimage für amd64/arm64
   nach GHCR und Installer, Pythonpakete und Updatearchive zum Release hochgeladen.
5. `papagui-update.json` wird zuletzt hochgeladen. Ohne diese Datei gibt es kein
   automatisch installierbares Update, auch wenn der Releaseeintrag bereits sichtbar ist.

Der Tag muss zur Paketversion passen. Ein vorhandenes Asset mit anderem Inhalt
wird niemals überschrieben. Bei einem korrigierten Build eine neue Version veröffentlichen.
Der GitHub-Token des Workflows benötigt `contents: write` und für den Imagejob
`packages: write`; diese Rechte sind im Workflow begrenzt deklariert. GHCR muss
dem Repository das Schreiben erlauben. Für frei zugängliche Downloads müssen
sowohl das Repository als auch das GHCR-Paket öffentlich sein.

## Desktop: Windows, Linux und macOS

Installierte Programme prüfen beim Start und alle sechs Stunden auf neue Releases.
Downloads erfolgen im Hintergrund, mit HTTPS, begrenzter Größe, GitHub-Asset-Metadaten
und SHA-256-Prüfsummen. Der Token für private Repositories wird bei Weiterleitungen entfernt.
Archive dürfen das Updateverzeichnis nicht verlassen.

Client und Tray werden gemeinsam vorbereitet. Der Wechsel erfolgt erst beim nächsten
Start, wenn beide geschlossen sind. Ein weiterlaufendes Tray kann ihn daher aufschieben.
Zuerst starten beide neuen Programme mit einem Wegwerfprofil zur Prüfung der nativen
Bibliotheken und Initialisierung. Anschließend wird das ruhende Benutzerprofil gesichert.
Externe Clientkonfigurationen werden zusätzlich kopiert; Dokumentquellen werden nicht verändert.

Programme liegen versionsweise neben dem Benutzerprofil, im Verzeichnis
`<Datenordner>-updates/versions`. Ein atomarer Zeiger wählt die aktive Version.
Der systemweite Installationsordner benötigt dafür keine Schreibrechte. Sicherungen
liegen unter `backups`, Diagnosen unter `status.json`, eine abgelehnte Version unter
`rejected.json`. Alte Versionen und Sicherungen werden zunächst bewusst aufbewahrt;
bei Platzmangel wird ein Update aufgeschoben.

Scheitert der reale Programmstart vor der Bereitschaftsmeldung, wird für den nächsten
Start die vorherige Programmversion ausgewählt. Dabei werden mögliche Benutzereingaben
nicht durch eine alte Sicherung überschrieben. Die gesicherten Profildaten bleiben zur
manuellen Wiederherstellung verfügbar. Ein neuerer manuell installierter Stand hat Vorrang.

`PAPAGUI_AUTO_UPDATE=0` stoppt Download und Aktivierung weiterer Updates.
`--offline` verhindert den Netzwerkcheck. Der bisher aktive Stand bleibt startbar.
Es gibt keine erzwungene Beendigung einer geöffneten Anwendung.

## Server: einmalige Einrichtung

Der Server aktualisiert seinen eigenen laufenden Container nicht. Ein separater
Prozess auf dem Docker-Host verwendet Compose. Voraussetzungen: Docker Compose v2,
Python 3.11+ bei direktem Hostbetrieb und genau ein bestehender Servercontainer mit
Bind-Mounts für `/data`, `/config` sowie einem schreibgeschützten `/source`.
Benannte Volumes und mehrere Replikate werden nicht automatisch migriert.
Der bestehende Server muss mindestens auf dem geprüften Ausgangsstand 0.4.3
stehen. Ältere Installationen werden vor dem ersten automatischen Update einmalig
mit Sicherung und Abnahme aktualisiert; der Updater hält sie nicht eigenständig an.

Die tatsächlichen absoluten Pfade und der bestehende Compose-Projektname müssen in
`deploy/updater/updater.example.json` angepasst werden. Ein fremdes Deployment darf
nicht unter einem neuen Projektnamen nochmals gestartet werden. Der Dienst prüft die
konfigurierten Pfade gegen die Mounts des bestehenden Containers.

Beispiel für eine neue, getrennte Linux-/Synology-Ablage:

```text
/volume1/docker/papagui/
  application/      Repository und Server-Compose-Datei
  data/             vorhandene Serverdaten
  config/           vorhandene Serverkonfiguration und API-Token
  updates/          private Sicherungen und Transaktionsstatus
  updater-config/  updater.json, gegebenenfalls GitHub-Lesezugang
```

Die Originaldokumente liegen außerhalb dieses Verzeichnisses. Bestehende Daten
werden für die Einrichtung nicht automatisch verschoben; bei vorhandenen
Installationen stattdessen die echten Pfade konfigurieren.

Direkt auf einem Linux-Host zunächst einmal ausführen:

```sh
python3 /volume1/docker/papagui/application/tools/server_update.py \
  --config /volume1/docker/papagui/updater-config/updater.json
```

Für den Dauerbetrieb `--watch` ergänzen oder die Vorlage
`deploy/updater/papagui-updater.service` mit den tatsächlichen Pfaden installieren.
Die Standardprüfung erfolgt stündlich. Bei abweichender Server-UID benötigt der
Dienst ausreichende Rechte, um Dateieigentümer beim Sichern und Wiederherstellen
beizubehalten; der bereitgestellte Container läuft dafür als root.

Alternativ auf Linux/Synology den separaten Container verwenden:

```sh
export PAPAGUI_DEPLOYMENT_ROOT=/volume1/docker/papagui
docker compose -p papagui-updater \
  -f /volume1/docker/papagui/application/deploy/updater/compose.yaml up -d --build
```

Der Updater benötigt den Docker-Socket und das dedizierte Deploymentverzeichnis
unter **demselben absoluten Pfad** wie auf dem Host. Er benötigt dessen Elternpfad
zum Umbenennen der Datenordner beim Rollback. Nur `/data` selbst einzubinden reicht
nicht aus. Der Docker-Socket gewährt Verwaltungsrechte über Docker; er wird niemals
in den normalen Servercontainer eingebunden. Unter Docker Desktop müssen die Pfade
mit der Linux-Daemon-Sicht übereinstimmen; ein Windows-Laufwerkspfad ist dort nicht
direkt ein Linuxpfad. Die konkrete Installation muss deshalb passend zum Host erfolgen.

### Ablauf und Wiederherstellung

Das Image wird zuerst anhand des unveränderlichen Registry-Digests geladen. Danach
stoppt nur der konfigurierte Serverdienst. Daten und Konfiguration werden offline
kopiert, SQLite-Sicherungen auf Integrität geprüft und Dateieigentümer erhalten.
Der neue Server startet mit einer Sperrdatei: außer `/health` beantwortet die API
Anfragen mit 503, bis Version und Datenbankintegrität geprüft sind.

Bei Erfolg wird zuerst die Transaktion festgeschrieben und danach die Sperre
entfernt. Bei einem Fehler vor diesem Punkt werden die gesicherten Daten und das
vorherige Image wieder eingesetzt. Der fehlgeschlagene Datenstand bleibt als
`.failed-<ID>` erhalten. Ein Prozessabbruch wird anhand des Transaktionsjournals
beim nächsten Start fortgesetzt. Nach einer bereits festgeschriebenen Transaktion
werden neu eingegangene Benutzeränderungen nicht zurückgesetzt.

Der Server ist während Stop, Sicherung und Prüfung kurz nicht erreichbar. Clients
verwenden ihre vorhandenen Offline-Daten. Dokumentquellen bleiben schreibgeschützt.
Unabhängige Backups sind weiterhin nötig: die lokalen Updatesicherungen schützen
nicht gegen einen Defekt des Datenträgers.

## Privates Repository und Kompatibilität

Solange das Repository privat ist, benötigt jeder Updater ausdrücklich eingerichteten
Lesezugriff. `PAPAGUI_RELEASE_TOKEN_FILE` verweist auf eine lokal geschützte Datei mit
einem GitHub-Token für Release-/Contents-Lesezugriff. Keinen gemeinsamen Token in
Installer, Repository oder Images einbauen. Für ein privates GHCR-Paket benötigt der
Server-Updater zusätzlich separat eingerichteten Registry-Lesezugriff; der GitHub-
Release-Token meldet Docker nicht automatisch an. Beim Containerbetrieb geschützte
Zugangsdaten gezielt einbinden und `DOCKER_CONFIG` passend setzen. Bei öffentlichen
Releases und Images werden diese Zugangsdaten nicht benötigt.

Automatische Updates akzeptieren nur Datenepoche 1, API v2/v1 und den
Kompatibilitätsausgangspunkt 0.4.3. CI vergleicht das OpenAPI-Schema und prüft
über echtes HTTP alten Client mit neuem Server sowie neuen Client mit altem Server.
Der Docker-Smoke prüft zusätzlich echtes Update, Schreibsperre und Rollback mit
temporären Daten. Inkompatible Änderungen dürfen nicht in diesem Releasezug
erscheinen; sie benötigen einen gesonderten, ausdrücklich geplanten Migrationsweg.
Diese Prüfungen sind eine Freigabebedingung, keine Garantie für beliebige zukünftige
Codeänderungen. Ungeprüfte Datenmigrationen sind ausdrücklich nicht freigegeben.

Die Programme bleiben derzeit unsigniert. SHA-256 mit authentifiziertem GitHub-
Download prüft Integrität, ersetzt aber keine unabhängige Release-Signatur oder
Windows-/Apple-Codesignatur. Die offenen Freigabepunkte stehen in [releasing.md](releasing.md).
