# Sicherheitskonzept

## Schutzgüter

- Kundendaten und Dokumentmetadaten
- Integrität von Index- und Kundengenerationen
- Nachvollziehbarkeit automatischer Kundenzuordnungen
- Verfügbarkeit des letzten gültigen Offlinebestands
- Client- und zukünftige Release-Schlüssel

## Vertrauensgrenzen

Der Server ist die einzige schreibende Instanz für Index und `customers.db`.
Clients erhalten unveränderliche, SHA-256-geprüfte Snapshots. Lokale
Offlineänderungen liegen in einer getrennten Outbox und verändern keinen
Server-Snapshot.

Die Dokumentquelle wird im Container read-only eingebunden. `/data` und
`/config` sind getrennte schreibbare Volumes. Ein Client benötigt keinen
direkten Zugriff auf diese Volumes und keine Dockerberechtigung.

## API und Authentifizierung

- `/health` enthält keine vertraulichen Daten und ist ohne Token erreichbar.
- Reguläre API-Aufrufe benötigen einen zufälligen Client-Bearer-Token.
- Indexaktionen, Einstellungen, Erkennungsläufe und Serverneustart benötigen
  denselben Client-Bearer-Token, aber kein Kennwort und keine Adminsitzung.
- Im Netzwerkbetrieb ist HTTPS über einen Reverse Proxy verpflichtend.

Clienttokens werden dem Prozess über eine geschützte Betriebskonfiguration oder
ein Secret übergeben und nicht in Generationen abgelegt. Die Compose-Dateien
reichen nur `PAPAGUI_API_TOKEN_FILE=/config/api-token` an den Container; der
Wert erscheint dadurch nicht in `docker inspect`. Direkte Umgebungswerte
bleiben für einen bewusst manuell gestarteten Server kompatibel, haben dort
Vorrang und sollten im Produktionsbetrieb nicht verwendet werden.

Der lokale Entwicklungsstart erzeugt Git-ignorierte Secretdateien. Linux und
macOS setzen bei jedem Start `0600` und brechen bei einem Rechtefehler ab. Unter
Windows entfernt der Starter best-effort die ACL-Vererbung und berechtigt nur
den aktuellen Benutzer; schlägt `icacls` fehl, erscheint eine eindeutige
Warnung, die vor einem Netzwerkbetrieb geklärt werden muss. Das Clienttoken
bleibt zusätzlich im Clientprozess erforderlich. Ein OS-Keyring-Adapter ist
noch nicht Teil von 0.4.2. Eine Rotation ersetzt zuerst die Serverdatei und
anschließend die Clientkonfigurationen kontrolliert.

Der Windows-Starter prüft Exitcode und Rückgabewert der Token-Erzeugung, bevor
er eine neue Secretdatei schreibt. Ein fehlgeschlagener Generator darf keinen
leeren Token als erfolgreich eingerichtete Authentifizierung hinterlassen.

## Konflikte und Wiederholungen

Kundenänderungen enthalten Basisrevision und Idempotency-Key. Eine veraltete
Revision führt zu `409 Conflict`; fremde Änderungen werden niemals still
überschrieben. Ein wiederholter Idempotency-Key erzeugt keine zweite Mutation.

## Dateisystem und Datenbanken

- Alle Archivpfade werden vor dem Entpacken auf Path Traversal geprüft.
- `source_id` und relative Pfade dürfen das konfigurierte Clientroot nicht
  verlassen.
- SQLite-Schreibvorgänge liegen hinter einer Unit of Work und werden atomar
  committed oder zurückgerollt.
- Datenbankmigrationen erstellen vorab ein SQLite-Backup.
- Fehlgeschlagene Generationen werden nie aktiviert und rotieren keine
  funktionierenden Backups.
- Externe Werkzeuge erhalten nur explizite Dateipfade und feste Zeitlimits.

## Logging und Datenschutz

Logs dürfen keine Tokens, kompletten Dokumentinhalte oder unnötige
personenbezogene Daten enthalten. Fehlermeldungen nennen nur die für Diagnose
erforderlichen Pfade und Metadaten. Zugriff auf Server- und Clientdatenordner
ist auf die jeweiligen Betriebskonten zu beschränken.

Für Entwicklung, Tests und Assistenzkontext werden ausschließlich synthetische
Dokumente und erfundene Kontakte verwendet. Konkrete Kundendaten, Quelldokumente,
Produktivdatenbanken, Belegauszüge und kundenbezogene Logpfade dürfen nicht in
Assistenz- oder externe Modellkontexte übernommen werden. Notwendige
Bestandsprüfungen bleiben lokal; für die technische Diagnose werden nur
anonymisierte, aggregierte Ergebnisse verwendet.

Auch der Architekturgraph wird ausschließlich aus den Python-Laufzeitquellen in
`packages/{contracts,server,client}/src` erstellt. Ein Scan der Repositorywurzel
oder alter Berichte und Graph-Memories ist kein freigegebener Ersatz dafür.
Die automatische Dokumentauslesung und Kundenerkennung laufen lokal auf dem
Server; eine externe Modellstufe ist nicht Bestandteil der Pipeline.

## Lieferkette

CI prüft getrennte Dependency-Locks, Paketgrenzen und Artefaktinhalt. Push- und
PR-Workflows erzeugen unsignierte Client-Testartefakte und prüfen Serverimages.
Der Releaseworkflow für stabile Veröffentlichungen kann nach erfolgreicher
Prüfung Images, Installer und ein Updatemanifest veröffentlichen. Automatische
Updates ab 0.5.0 prüfen HTTPS-Herkunft, GitHub-Asset-Metadaten, Prüfsummen und
Kompatibilitätsangaben; Ablauf und Rückfallmechanismen stehen unter
[Automatische Updates](docs/automatic-updates.md). Eine separate kryptographische
Signatur des Updatemanifests sowie Code Signing und Apple-Notarisierung sind
noch nicht eingerichtet. Eine erfolgreiche lokale Prüfung bestätigt weder
einen entfernten CI-Lauf noch die native Abnahme auf allen Plattformen.

## Offene Hardening-Punkte

- Zertifikate und Signaturprozess für Clientartefakte
- vollständige native Third-Party-Inventare und zugehörige Quellbereitstellung;
  die Projektlizenz ist bereits GPL-3.0-or-later
- [Beta-Abnahme](docs/beta-readiness.md) einschließlich Quellidentität,
  Netzwerkbetrieb, privatem Sicherheitsmeldeweg und Signierung
- Tokenrotation ohne Wartungsfenster
- ACL-Prüfung für konkrete Synology-Freigaben
- Security-Review vor einer zukünftigen IMAP-Integration
