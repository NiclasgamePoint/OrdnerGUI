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
Die neuen 0.4.2-Komponententags sind kein veröffentlichtes Release.

## Quellpfade

Generationen speichern keine absoluten `/source`-Pfade. Jeder Treffer enthält
eine validierte `source_id` und einen POSIX-relativen Pfad. Der Client löst
diese Werte über sein lokales Mapping auf und verhindert Pfadtraversal.

## Quellidentität und Mountschutz

Der Server schreibt nie in den read-only Quelldatenträger. Bei der ersten
zugelassenen Initialisierung speichert er stattdessen eine portable Auswahl von
Wurzeleinträgen als `source-identity.json` im Configvolume. Vor jedem Lauf muss
die Quelle nicht leer sein und zu mindestens einem gespeicherten Eintrag passen.
Ein leer vorhandener Mountpoint oder ein ausgetauschter Mount wird dadurch vor
Scan, Löschung und Generationsaktivierung abgewiesen. Zur bewussten
Neuprovisionierung wird die Identität nach Sicherung des Configvolumes explizit
neu initialisiert.
