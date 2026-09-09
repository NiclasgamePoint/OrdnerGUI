# Daten, Generationen und Migration

## Generationsformat v2

Das aktuelle Manifest referenziert zwei unabhängig wechselnde Komponenten:

- `components.index`: portabler SQLite-Katalog samt FTS-Inhaltsindex
- `components.customers`: konsistenter SQLite-Snapshot von `customers.db`

Jede Komponente enthält Kennung, Erstellzeit, Archivname, Größe, SHA-256 und die
Prüfsummen ihrer Nutzdateien. Eine angenommene Kundenänderung veröffentlicht nur
eine neue Kundenkomponente. Ein vollständiger Indexlauf führt anschließend die
Kundenerkennung aus, staged beide Komponenten und aktiviert das Paar mit genau
einem atomar ersetzten `active-generation.json`. Fehler vor diesem Commit
verwerfen beide Stages und lassen den vollständigen vorherigen Stand aktiv. Eine
fehlgeschlagene spätere Bereinigung macht den gültigen Stand nicht ungültig.

## Aufbewahrung

Server und Client bewahren pro Komponente den aktiven Stand und drei vorherige
Generationen auf. Der Server rotiert Archive und Deskriptoren anhand der
zeitlich sortierbaren Generationskennung. Der Client führt dieselbe Historie in
`active-generation.json` und entfernt danach nicht mehr referenzierte
Generationsverzeichnisse.

Die serverseitige Anzahl von drei Vorgängern ist eine feste Produktinvariante
und nicht konfigurierbar. Schlägt das Löschen wegen eines Dateisystemfehlers
fehl, bleibt aus Fail-safe-Gründen vorübergehend mehr Historie liegen: Die
aktive Generation wird niemals für die Bereinigung ausgewählt. Der Fehler wird
im Serverstatus unter `retention` und dauerhaft in
`generations-v2/retention-status.json` diagnostiziert. Vor und nach der nächsten
Publikation wird die Bereinigung deterministisch erneut versucht; erst nach
erfolgreicher Reparatur wechselt der Status wieder auf `ok`.

## Migration von 0.4.1

1. Serverdaten- und Configvolume sichern.
2. `customers.db` mit SQLite Backup API kopieren und die Schemamigration in
   einer Transaktion ausführen.
3. Den Index vollständig neu bauen, weil v1 absolute Containerpfade enthält.
4. Erst nach Integritätsprüfung das v2-Manifest atomar aktivieren.
5. Im Client einen vorhandenen `current`-Symlink einmalig lesen und in
   `active-generation.json` überführen.
6. Die ursprünglichen Daten bis zur erfolgreichen Validierung unverändert
   lassen; die Migration ist wiederholbar.

Die 0.4.2-Migration ist transaktional und wiederholbar. Sie erhält die
Legacy-Tabellen als Rückfallbeleg und überführt deren nutzbare Daten zusätzlich:

- Kundenordner, zusätzliche Ordner, Projektordner und Vorschlagsprovenienz werden
  in `source_id` plus relative POSIX-Pfade normalisiert. `/source/...` wird der
  primären Quelle zugeordnet; Windows-Laufwerke erhalten beispielsweise
  `legacy-c`, UNC-Freigaben eine stabile `unc-<host>-<share>`-Kennung und andere
  absolute POSIX-Pfade `legacy-posix`.
- `customer_projects` erhält portable Projektwurzel-, Service-, Stadt-, Jahres-
  und Provenienzfelder. Der Kunden-Snapshot enthält diese Projekte additiv.
- Historische `customer_data_suggestions` werden idempotent anhand ihres
  Fingerprints in `customer_document_suggestions` kopiert. Unterstützte
  Feldvorschläge und Kontaktvorschläge bleiben samt Status, Regel, Auszug,
  Konfidenz und portablem Quellpfad prüfbar.
- Historische Erkennungsfälle erhalten fehlende Reviewfelder und eine portable
  Ersatzwurzel; Entscheidungen und Laufhistorie bleiben erhalten.

Golden-Fixtures prüfen die Datenwerte und Pfadtransformationen, nicht nur
Tabellenanzahlen. Die vor der Migration erzeugte SQLite-Sicherung bleibt
unverändert und kann atomar zurückgespielt werden.

Ein Rollback verwendet das gesicherte Servervolume und den Tag `v0.4.1`.
Ein Komponententag stellt für sich noch kein veröffentlichtes Release dar.

## Quellpfade

Generationen speichern keine absoluten `/source`-Pfade. Jeder Treffer enthält
eine validierte `source_id` und einen POSIX-relativen Pfad. Der Client löst
diese Werte über sein lokales Mapping auf und verhindert Pfadtraversal.

## Vorschläge, Entscheidungen und serverweite Sperrliste

Die additive Erkennungsmigration erweitert `customer_document_suggestions` um
normalisierte Werte, Partei, Qualitätsstufe, Lebenszyklus und Versionsangaben.
`candidate_aliases` ordnet alte Fingerprints zu; `candidate_evidence` bewahrt die
mehreren Fundstellen eines Vorschlags. `candidate_decisions` erhält frühere
Entscheidungen. Kontakte besitzen eine stabile `uid`, und
`customer_field_provenance` trennt manuelle, angenommene und unbekannte Herkunft.
Bestehende Werte werden dadurch nicht nachträglich als bestätigt ausgegeben.

`customer_recognition_status` hält den Abdeckungszustand je Kunde.
`recognition_blocklist` speichert die globalen Ausschlüsse mit Typ, Rohwert,
normalisiertem Wert, Grund und Erstellzeit. Ein bestätigter Batch validiert und
speichert alle Ergänzungen und Löschungen in einer Transaktion und aktualisiert
die Sichtbarkeit vorhandener Vorschläge. Manuell gepflegte Kundendaten und
frühere Entscheidungen bleiben dabei erhalten.

Speicherung und Veröffentlichung sind zwei Schritte. Ein dauerhafter Marker in
`candidate_schema_metadata` kennzeichnet eine noch ausstehende
Kundenveröffentlichung. Meldet die API `published=false`, sind die Änderungen
bereits gespeichert; ein erneuter Batch, auch ohne weitere Änderungen, versucht
die Veröffentlichung erneut. `publication_pending` macht diesen Zustand beim
späteren Laden wieder sichtbar. Ein Versionsmarker verhindert, dass eine ältere
Veröffentlichung eine inzwischen neuere Änderung versehentlich quittiert.

Sperrlisten-IDs werden anhand einer dauerhaft gespeicherten Obergrenze vergeben
und nach Löschung nicht wiederverwendet. Ein verspäteter Löschversuch kann damit
keinen neu angelegten, anderen Eintrag treffen.

## Wechsel des Entwicklungsrechners

Git enthält Code, Contracts, Migrationslogik und synthetische Fixtures. Dokumente,
Serverdatenbanken, Sperrliste, Tokens und lokale Clienteinstellungen werden nicht
mitgeklont. Bei Weiterbetrieb desselben Servers genügt die neue
Clientkonfiguration mit passender Plattform-Pfadzuordnung. Für einen Serverumzug
müssen `/data` und `/config` gemeinsam konsistent gesichert und wiederhergestellt
werden; siehe [Backup und Restore](server/operations.md#backup-und-restore).

## Quellidentität und Mountschutz

Der Server schreibt nie in den read-only Quelldatenträger. Bei der ersten
zugelassenen Initialisierung speichert er stattdessen eine portable Auswahl von
Wurzeleinträgen als `source-identity.json` im Configvolume. Vor jedem Lauf muss
die Quelle nicht leer sein und zu mindestens einem gespeicherten Eintrag passen.
Ein leer vorhandener Mountpoint oder ein ausgetauschter Mount wird dadurch vor
Scan, Löschung und Generationsaktivierung abgewiesen. Zur bewussten
Neuprovisionierung wird die Identität nach Sicherung des Configvolumes explizit
neu initialisiert.
