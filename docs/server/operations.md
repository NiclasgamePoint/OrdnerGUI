# Serverbetrieb mit Docker und Synology

## Volumes und Rechte

Der Container verwendet drei klar getrennte Pfade:

| Containerpfad | Rechte | Inhalt |
| --- | --- | --- |
| `/source` | read-only | Gemountete Bauvorhaben/Dokumente |
| `/data` | read-write | Index, `customers.db`, privater Extraktionscache, Generationen, Backups und Jobstatus |
| `/config` | read-write | Servereinstellungen, Quellidentität und API-Token |

Nur eine Serverinstanz darf dasselbe `/data`-Volume verwenden. Die konfigurierte
UID/GID benötigt Leserechte auf der Quelle und Schreibrechte auf Daten und
Konfiguration.

Die Compose-Datei bindet Port 8765 standardmäßig ausschließlich an
`127.0.0.1`. `PAPAGUI_API_BIND_ADDRESS=0.0.0.0` ist nur für einen bewusst
abgesicherten direkten LAN-Zugriff vorgesehen. `/config/api-token` wird als
UTF-8-Datei gelesen; sein Wert ist
nicht Bestandteil der Containerumgebung und deshalb nicht über
`docker inspect` sichtbar.

## Lokaler Start

```bash
cp deploy/server/.env.example deploy/server/.env
```

In `.env` werden absolute Quell-, Daten- und Configpfade, `source_id`, UID/GID
und die Bind-Adresse eingetragen. Secrets gehören weder in Git noch in `.env`
oder Compose-Dateien. Vor dem ersten Start wird zunächst das Image gebaut:

```bash
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml build
```

Danach die Tokendatei erzeugen:

```bash
PAPAGUI_SECRET_DIR=/volume1/docker/papagui/config
mkdir -p "$PAPAGUI_SECRET_DIR"
chmod 700 "$PAPAGUI_SECRET_DIR"
umask 077
docker run --rm --entrypoint python papagui-server:0.4.2 \
  -c 'import secrets; print(secrets.token_urlsafe(32))' \
  > "$PAPAGUI_SECRET_DIR/api-token"
chmod 600 "$PAPAGUI_SECRET_DIR/api-token"
```

Nun starten und prüfen:

```bash
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml up -d
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml ps
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml logs -f papagui-server
```

Der Healthcheck prüft `GET /health`. HTTP 200 bestätigt die Erreichbarkeit;
auch bei nicht verfügbarer Quelle kann der Inhalt `degraded` melden.
Fachlicher Indexfortschritt, Workerzahlen und letzte Fehler stehen unter
`/v2/server/status` und im Client-Tray.

## Beenden und Neustarten

```bash
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml stop papagui-server
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml start papagui-server
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml down
```

Die Compose-Interpolation für `down` verwendet sichere Defaults und darf nicht
von einer aktuell gemounteten Datenquelle abhängen. Das Tray kann einen
kontrollierten Prozessneustart anfordern; `restart: unless-stopped` startet den
Container anschließend wieder.

Der gleiche Pfad wird vor einem Release mit `bash tools/docker_smoke.sh`
geprüft. Der Smoke-Test beobachtet den Prozess-Exitcode 75, den anschließenden
automatischen Containerneustart und den Fortbestand von Indexgenerationen und
Servereinstellungen. Außerdem stellt er sicher, dass weder Qt, `app` noch das
Clientpaket im Serverimage importierbar sind.

## Erstkonfiguration

- Den Wert aus `api-token` über einen geschützten Kanal in die
  Clientkonfigurationen übernehmen. Die Datei selbst nicht in eine gemeinsame
  Benutzerfreigabe legen.
- Für Docker/NAS ausschließlich die Tokendatei verwenden. Die direkte Variable
  `PAPAGUI_API_TOKEN` bleibt für gezielte manuelle Starts kompatibel;
  ihr Wert ist über Containerinspektion sichtbar. Ein zusätzliches
  Adminpasswort wird nicht konfiguriert.
- Automatisches Indexintervall zwischen 15 Minuten und 48 Stunden wählen.
- Reverse Proxy und HTTPS konfigurieren, bevor die API außerhalb eines
  vertrauenswürdigen Netzes erreichbar ist.

Beim ersten erfolgreichen Lauf speichert der Server in
`/config/source-identity.json` eine Identität des nicht leeren Quellroots. Ein
leerer oder erkennbar ausgetauschter Mount wird danach abgewiesen und niemals
als Löschung aller Dokumente veröffentlicht. Bei einem absichtlichen Wechsel
zuerst Backup anlegen, den neuen Mount manuell prüfen und die bestehende
Identitätsdatei in eine `.previous`-Datei umbenennen; erst dann darf sie durch
einen beaufsichtigten Lauf neu angelegt werden.

## Zeitplanung und Ressourcen

Die dauerhaften Servereinstellungen werden über den Client oder
`GET/PUT /v2/admin/settings` verwaltet. Standardmäßig sind automatische
Läufe und der tägliche Inhaltsabgleich aktiv, das Intervall beträgt
86.400 Sekunden. `PAPAGUI_INDEX_INTERVAL_SECONDS` liefert lediglich den
Startstandard; eine gespeicherte Einstellung hat Vorrang.

Ein regulärer Lauf gleicht alle Dateipfade ab, übernimmt aber unveränderte
erfolgreich verarbeitete Dokumente ohne erneutes Lesen. Der tägliche
Inhaltsabgleich prüft zusätzlich Inhaltshashes. Sein letzter erfolgreicher
vollständiger Katalogtermin wird dauerhaft gespeichert: Bei einem normalen
Intervall von 48 Stunden und einer vor 23 Stunden erfolgten Inhaltsprüfung
bleibt die nächste Prüfung in einer Stunde fällig. Kundenaufträge für
einzelne Projekte verschieben diesen Termin nicht.

`serve` startet normalerweise direkt einen Indexlauf. Bei einem gezielten
CLI-Start mit `--no-run-on-start` wird dieser ausgelassen. Fehlt der tägliche
Prüftermin oder ist er bereits überschritten, wartet der Scheduler dann bis
zum kleineren Wert aus normalem Intervall und 24 Stunden. Ein bevorstehender
gespeicherter Prüftermin bleibt erhalten. `automatic_runs_enabled=false`
unterdrückt automatische Termine; ausdrücklich angeforderte Läufe sind
weiter möglich. `daily_reconciliation_enabled=false` deaktiviert die
zusätzliche Inhaltsprüfung.

Indexierung und auslesende Erkennungsaufträge nutzen dieselbe begrenzte
Dokumentpipeline. Das Profil `gentle`, `balanced` oder `fast` verwendet
15 %, 25 % beziehungsweise 60 % der effektiven CPU-Kapazität für die
Berechnung der Workerzahl. CPU-Affinität, cgroup-Grenzen, verfügbarer RAM,
Speicherreserve und Dokumentbudget begrenzen diese weiter auf 1–20 Worker;
bei unbekanntem oder knappem RAM bleibt ein Worker. Die Profile begrenzen
die Anzahl paralleler Aufgaben, garantieren aber keine feste
CPU-Auslastung. Die mitgelieferte Compose-Datei setzt selbst keine
CPU-/RAM-Limits; konfigurierte Containergrenzen werden berücksichtigt.

Die Warteschlange enthält höchstens doppelt so viele ausstehende Dokumente
wie Worker. Office-/PDF-Parser laufen isoliert mit Zeit- und unter Linux
Speichergrenzen. OCR verwendet einen OpenMP-Thread und die eingestellten
Seiten-/Bildbudgets. Ein Abbruch stoppt weitere Aufgaben und überwachte
Parser-/OCR-Prozesse; die letzten veröffentlichten Generationen bleiben
verfügbar. Workerstatistiken enthalten nur Summen und Laufzeiten.

`/data/extraction/artifacts.db` enthält private Layout-/OCR-Ergebnisse.
Der Cache verwendet Inhaltshash und relevante Parser-/Werkzeug- sowie
Einstellungsversionen. Er gehört zum Serverbackup, wird aber nicht als
großes Layoutarchiv an Clients verteilt. Dateigröße, Extraktionsbudget,
Cachebudget und Aufbewahrung werden in den Servereinstellungen verwaltet.
Eine angezeigte Teilabdeckung kann bedeuten, dass eine dieser Grenzen
erreicht wurde; sie ist kein Beweis für fehlende Kundenangaben.

## Administration und Veröffentlichungswiederholung

Alle folgenden Aktionen verwenden denselben Client-Token. Es gibt kein
zusätzliches Adminpasswort.

| Aktion | Wirkung |
| --- | --- |
| Regulärer Indexlauf | Dateiabgleich und fällige Dokument-/Kundenverarbeitung; unveränderte Ergebnisse werden übernommen. |
| Indexneuaufbau (`full_rebuild`) | Katalog neu erstellen und Inhaltshashes prüfen; passende Parser-/OCR-Cacheergebnisse bleiben verwendbar. |
| Texte erneut bewerten (`reassess`) | Vorhandene Extraktionstexte eines Kunden neu bewerten. |
| Dokumente neu lesen (`extract`) | Dokumente der zugeordneten Kundenprojekte erneut auslesen und bewerten. |
| Kundenerkennung vollständig neu aufbauen (`rebuild`) | Katalog und Dokumentauslesung einschließlich aktivierter OCR erneuern, alle Kunden ohne normales Projektsuchbudget bewerten. |
| Sperrlistenänderungen bestätigen | Gesammelte Sperren atomar speichern, bestehende Vorschläge einmal abgleichen und Kundenstand veröffentlichen. |

Dateitypen, Ordnerausschlüsse und Schutzgrenzen je Dokument gelten auch
beim vollständigen Erkennungsneuaufbau. Dieser erfordert eine aktivierte
`recognition_pipeline_enabled`-Einstellung und ein positives
`priority_documents_per_project`; der Wert 0 deaktiviert die Dokumentprüfung.
Kunden-/Neuaufbauaufträge sind
dauerhaft, werden beim Schließen des Clientfensters fortgesetzt und nach
einem Serverneustart erneut eingereiht, wenn sie unterbrochen wurden.
Ausdrücklich abgebrochene Aufträge bleiben abgebrochen.

Die Sperrliste darf während laufender Indexierung bearbeitet werden.
Vormerkungen im Client werden erst mit **Änderungen bestätigen** wirksam.
Der Stapelendpunkt verändert ausschließlich die Kundendatenbank und
veröffentlicht anschließend eine Kundenkomponente; er startet keine
OCR, Dokumentauslesung oder Neuaufbereitung des Index.

Meldet der Server nach dem Speichern `published: false`, gelten die
Sperren bereits auf dem Server, während Offline-Clients noch einen älteren
Snapshot besitzen können. Die offene Veröffentlichung überlebt
Server-/Clientneustarts und erscheint beim Laden als
`publication_pending: true`. Nach Beheben des Veröffentlichungsfehlers
im Client **Veröffentlichung wiederholen** wählen oder einen leeren
Stapel an `POST /v2/admin/recognition/blocklist/batch` senden. Es ist kein
Erkennungsneuaufbau erforderlich. Ein unveränderter Stapel ohne offenen
Veröffentlichungsschritt erzeugt keine weitere Generation. Details und
Fehlerantworten stehen in [der API-Referenz](../api.md).

## Backup und Restore

Die integrierte Generationenverwaltung hält aktiv plus drei Vorgänger. Sie
ersetzt kein externes NAS-Backup.

Für ein konsistentes externes Backup:

1. Laufenden Indexjob beenden oder abschließen lassen.
2. Container stoppen.
3. `/data` und `/config` gemeinsam sichern.
4. Container wieder starten und Healthcheck prüfen.

Beispiel für eine wiederherstellbare gemeinsame Sicherung auf dem NAS (den
Zeitstempel im Dateinamen bewusst setzen):

```bash
PAPAGUI_BACKUP_FILE=/volume1/backups/papagui/server-0.4.2-20260902T2100.tar.gz
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml stop papagui-server
tar -C /volume1/docker -czf "$PAPAGUI_BACKUP_FILE" papagui/data papagui/config
tar -tzf "$PAPAGUI_BACKUP_FILE"
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml start papagui-server
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml ps
```

Restore:

1. Container stoppen.
2. Aktuelle Volumes zusätzlich sichern.
3. Gewünschte Sicherung nach `/data` und `/config` zurückspielen.
4. Eigentümer und Rechte kontrollieren.
5. Server starten, SQLite-Integrität und `/health` prüfen.

Die vorhandenen Verzeichnisse beim Restore nicht löschen, sondern zunächst
umbenennen. Damit ist ein fehlgeschlagener Restore direkt rücknehmbar:

```bash
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml stop papagui-server
mv /volume1/docker/papagui/data /volume1/docker/papagui/data.before-restore
mv /volume1/docker/papagui/config /volume1/docker/papagui/config.before-restore
tar -C /volume1/docker -xzf /volume1/backups/papagui/server-0.4.2-20260902T2100.tar.gz
chown -R 1026:100 /volume1/docker/papagui/data /volume1/docker/papagui/config
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml up -d
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml exec papagui-server \
  python -c "import sqlite3; c=sqlite3.connect('/data/customers.db'); assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'"
```

Erst nach erfolgreichem Healthcheck, SQLite-Prüfung und einem Testclient dürfen
die `.before-restore`-Verzeichnisse nach der betrieblichen Aufbewahrungsfrist
entfernt werden.

Der Docker-Smoke validiert `snapshot_sqlite` und den atomaren Restore an
temporären Kopien der Kundendatenbank. Er überschreibt dabei niemals die aktive
`customers.db`; der betriebliche Restore bleibt weiterhin ein bewusstes
Offline-Verfahren nach den obigen Schritten.

## Upgrade und Rollback

Vor einem Upgrade werden beide Volumes gesichert. Datenmigrationen laufen
idempotent und aktivieren neue Generationen erst nach vollständiger Prüfung.
Ein Rollback auf 0.4.1 benötigt das dazugehörige alte Volume-Backup; das
Generationsschema v2 wird vom Legacyserver nicht geschrieben.

Konkreter Upgradeablauf für ein lokal gebautes, noch nicht veröffentlichtes
Image:

1. Server-/Configbackup wie oben erstellen und validieren.
2. Gewünschten `server-v<version>`-Quellstand auschecken.
3. `docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml build --pull`.
4. `docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml up -d`.
5. Health, Serverversion, letzten Indexstatus und einen Clientdownload prüfen.

Rollback bedeutet immer Code **und** das vor dem Upgrade gemeinsam gesicherte
`data`/`config`-Paar zurückzunehmen. Nur das alte Image gegen bereits migrierte
Volumes zu starten ist nicht freigegeben. Den Restore wie oben mit umbenannten
aktuellen Verzeichnissen durchführen und anschließend das vorherige Image neu
bauen/starten.

## Synology

Das Zielimage wird für `linux/amd64` und `linux/arm64` vorbereitet. ARMv7 ist
nicht vorgesehen. Über SSH liefert `id <dienstkonto>` die numerische UID/GID.
Diese Werte werden in `.env` eingetragen; danach gehören Daten und Config dem
Dienstkonto, die Quelle benötigt nur Leserechte:

```bash
id papagui
mkdir -p /volume1/docker/papagui/data /volume1/docker/papagui/config
chown -R 1026:100 /volume1/docker/papagui/data /volume1/docker/papagui/config
chmod 750 /volume1/docker/papagui/data /volume1/docker/papagui/config
chmod 600 /volume1/docker/papagui/config/api-token
```

Im Container Manager werden dieselben absoluten NAS-Pfade eingetragen;
`/source` bleibt read-only. Empfohlene DSM-Konfiguration:

1. Unter **Systemsteuerung → Sicherheit → Zertifikat** ein gültiges Zertifikat
   für den Servernamen importieren oder per Let's Encrypt ausstellen.
2. Unter **Anmeldeportal → Erweitert → Reverse Proxy** eine HTTPS-Quelle auf
   Port 443 und als Ziel `http://127.0.0.1:8765` anlegen; dort das Zertifikat
   zuordnen.
3. In der DSM-Firewall TCP 443 nur für die benötigten Clientnetze erlauben.
   Port 8765 nicht freigeben; `PAPAGUI_API_BIND_ADDRESS=127.0.0.1` beibehalten.
4. Clients auf die HTTPS-URL konfigurieren und Zertifikatsprüfung nicht
   deaktivieren.

Falls ein separates Reverse-Proxy-System den NAS über das LAN erreichen muss,
darf die Bind-Adresse gezielt auf eine interne NAS-IP oder `0.0.0.0` gesetzt
werden. Dann Port 8765 per DSM-Firewall ausschließlich für die Proxy-IP öffnen;
direkter unverschlüsselter Clientzugriff bleibt nicht freigegeben.

Die Quelldisk darf zeitweise fehlen. In diesem Zustand startet kein Indexlauf,
der Server darf den letzten gültigen Stand aber weiterhin ausliefern und darf
das Fehlen nicht als Massendeletion interpretieren.

## Diagnose

Die Telefonnummernerkennung kombiniert die lokale Rufnummernplanprüfung von
`phonenumbers` mit einer konservativen Formatprüfung: nationale Vorwahl mit `0`
oder internationale Vorwahl mit `+`/`00`, Leerzeichen, Schrägstrich, Klammern und
Bindestriche. Kompakte Inlandsnummern ohne Trennzeichen benötigen ein Telefon- oder
Mobil-Label. Punkte und Dezimalkommas innerhalb von Zahlen sowie daraus abgetrennte
Teiltreffer werden verworfen, auch bei Wiederholung. Benannte Durchwahlen wie
`ext. 42` bleiben erlaubt. Die Prüfung bestätigt keine Erreichbarkeit oder Zuordnung
zu einer Person; dafür gelten weiterhin die Belege und die manuelle Bestätigung.

Beim ersten Zugriff auf die Vorschlagsablage nach diesem Serverupdate werden alte
offene Vorschläge mit unzulässigen Zahlenformaten einmalig als ungültig ausgeblendet.
Ihre Belege bleiben als inaktive Historie erhalten. Bestätigte Kundendaten und
bereits getroffene Entscheidungen bleiben unverändert. **Neu bewerten** wendet
die neuen Kontextregeln auf vorhandene Dokumentauszüge an; **Neu auslesen** ist
für diese Korrektur nicht erforderlich.

Die Kundendatenerkennung verwendet Excel-Dateien nicht als Beleg für Telefonnummern,
auch bei Telefon-/Mobil-Spalten oder benannten Ansprechpartnern. Das gilt für XLS,
XLSX und die weiteren Excel-Arbeitsmappen-/Vorlagenendungen, einschließlich älterer
Textextraktionen ohne Zellinformationen. E-Mail-Adressen und andere Angaben werden
weiter ausgewertet; die Dokument-Volltextsuche bleibt vollständig.

Nach dem Serverupdate **Neu bewerten** beim betroffenen Kunden
ausführen. Alte Excel-Telefonbelege dieses Kunden entfallen auch dann, wenn die
jeweilige Arbeitsmappe außerhalb des Dokumentbudgets der erneuten Prüfung liegt.
Offene Vorschläge ohne verbleibenden Beleg werden als veraltet ausgeblendet;
unabhängige Belege aus anderen Dokumenten und bereits bestätigte Werte bleiben erhalten.

- `GET /health`: Prozess erreichbar
- `GET /v2/system/info`: Versionen und Fähigkeiten
- `GET /v2/server/status`: Laufphase, Fortschritt, Fehler und `document_workers`
- `GET /v2/admin/recognition/blocklist`: Sperren und offene Veröffentlichung
- `GET /v2/admin/recognition/rebuild`: letzter vollständiger Erkennungsauftrag
- `GET /v2/customers/{id}/recognition-status`: Kundenabdeckung und letzter Kundenauftrag
- Containerlogs: technische Start- und Laufzeitfehler
- Generationsmanifeste: Komponentenversionen und Prüfsummen

Nützliche Befehle:

```bash
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml ps
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml logs --tail=200 papagui-server
docker compose --env-file deploy/server/.env -f deploy/server/compose.yaml logs -f papagui-server
docker inspect --format '{{json .State.Health}}' papagui-server-papagui-server-1
```

Compose rotiert den lokalen `json-file`-Log bei 10 MiB und bewahrt drei Dateien.
DSM-Log Center/Container Manager kann zusätzlich eine zentrale, zugriffsgeschützte
Aufbewahrung übernehmen. Vor Supportexporten Secrets, Kundennamen und Quellpfade
prüfen und nötigenfalls schwärzen.

Tokens, Passwörter und vollständige Dokumentinhalte dürfen nicht in Logs
geschrieben werden.

`document_workers` unterscheidet entdeckte, verarbeitete, wiederverwendete,
gelesene, neu extrahierte und fehlgeschlagene beziehungsweise teilweise
verarbeitete Dokumente. Ein Hashabgleich kann Dokumente lesen und trotzdem
Cacheergebnisse verwenden. Aus den Zählern allein folgt deshalb keine
bestimmte Parser-/OCR-Laufzeit. Vergleichsmessungen für die Entwicklung
verwenden synthetische Dateien; die Laufzeit auf dem eigenen NAS muss
separat beobachtet werden.
