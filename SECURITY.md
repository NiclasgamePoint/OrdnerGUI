# Sicherheitskonzept

## Schutzgüter

- Kundendaten und Dokumentmetadaten
- Integrität von Index- und Kundengenerationen
- Nachvollziehbarkeit automatischer Kundenzuordnungen
- Verfügbarkeit des letzten gültigen Offlinebestands
- Client-, Admin- und zukünftige Release-Schlüssel

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
- Indexaktionen, Einstellungen und Serverneustart benötigen eine separate
  Adminsitzung.
- Die persistente Security-Konfiguration enthält einen Argon2id-Hash.
  Klartextpasswörter und Sitzungstokens werden nicht protokolliert oder in
  Generationen gespeichert.
- Adminsitzungstokens verbleiben im Speicher des Tray-Prozesses und werden beim
  kontrollierten Beenden widerrufen.
- Im Netzwerkbetrieb ist HTTPS über einen Reverse Proxy verpflichtend.

Clienttokens werden dem Prozess über eine geschützte Betriebskonfiguration oder
ein Secret übergeben und nicht in Generationen abgelegt. Die Compose-Dateien
reichen nur `PAPAGUI_API_TOKEN_FILE=/config/api-token` und
`PAPAGUI_ADMIN_PASSWORD_HASH_FILE=/config/admin-password-hash` an den Container;
die Werte erscheinen dadurch nicht in `docker inspect`. Direkte Umgebungswerte
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

Der lokale Komfortstart legt gegenwärtig außerdem ein Git-ignoriertes
Adminpasswort unter `docker-config/admin-password` ab. Auf dem Host wird daraus
vor dem Compose-Start über `hash-password --stdin` ein Argon2id-Hash erzeugt;
das Passwort steht dabei nie in der Python-Prozessargumentliste. Nur die
Hashdatei wird in `/config` gemountet. Auf dem NAS wird derselbe Hash vorab
argv-sicher erzeugt und als Datei mit Modus `0600` abgelegt.

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

Logs dürfen keine Tokens, Passwörter, kompletten Dokumentinhalte oder unnötige
personenbezogene Daten enthalten. Fehlermeldungen nennen nur die für Diagnose
erforderlichen Pfade und Metadaten. Zugriff auf Server- und Clientdatenordner
ist auf die jeweiligen Betriebskonten zu beschränken.

## Lieferkette

CI prüft getrennte Dependency-Locks, Paketgrenzen und Artefaktinhalt. Der
0.4.2-Stand ist nicht signiert und nicht
veröffentlicht. Öffentliche Releases, Container-Push, Code Signing,
Notarisierung und Auto-Updates bleiben bis zu einer gesonderten Freigabe
deaktiviert.

## Offene Hardening-Punkte

- Zertifikate und Signaturprozess für Clientartefakte
- endgültige Projektlizenz und vollständige Third-Party-Notices
- Tokenrotation ohne Wartungsfenster
- ACL-Prüfung für konkrete Synology-Freigaben
- Security-Review vor einer zukünftigen IMAP-Integration
