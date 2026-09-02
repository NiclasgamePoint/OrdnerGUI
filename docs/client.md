# Desktop-Client

## Verantwortung

Der Desktop-Client zeigt und bearbeitet Kunden, durchsucht den lokalen
Dokumentkatalog und synchronisiert Snapshots mit dem Server. Er baut selbst
keinen Index auf und schreibt niemals in eine heruntergeladene Generation.

## Lokaler Zustand

Der Clientdatenordner enthält:

```text
client-data/
├── active-generation.json
├── client-config.json
├── search-history.json
├── generations/
│   ├── index/<generation-id>/
│   └── customers/<generation-id>/
└── customer-outbox.db
```

`active-generation.json` verweist auf eine geprüfte Index- und
Kundengeneration. Der Wechsel erfolgt mit einem atomaren Dateiersatz und ist
dadurch auch unter Windows ohne Symlink-Rechte sicher. Aktiv bleiben höchstens
der aktuelle Stand und drei Vorgänger; referenzierte Komponenten werden nie
vorzeitig entfernt.

## Synchronisation und Offlinebetrieb

Beim Start und anschließend im konfigurierten Intervall fragt der Client zuerst
das v2-Manifest ab und fällt nur bei einer nicht vorhandenen v2-Route auf einen
echten kombinierten v1-Download zurück. Vor der Aktivierung prüft er das
Generationsschema, Archivgröße und Archiv-SHA-256. Ein v2-Archiv muss außerdem
eine interne `manifest.json` für den richtigen Komponententyp enthalten; alle dort
deklarierten Nutzdateien werden auf Pfad, Größe und Prüfsumme geprüft. Kombinierte
v1-Archive bleiben über den ausdrücklichen Legacy-Pfad kompatibel und werden nur
einmal heruntergeladen. Bei Netzwerk-, Format- oder Prüfsummenfehlern bleibt der
bisherige Stand aktiv. Ein nach der atomaren Zeigeraktivierung fehlschlagendes
Aufräumen alter Generationen macht den neuen Zeiger nicht ungültig; die Bereinigung
wird sofort erneut und zusätzlich beim nächsten Wechsel versucht. Bleiben beide
Löschversuche wegen einer Dateisperre oder eines OS-Fehlers erfolglos, wird eine
`GenerationRetentionWarning` ausgegeben und der Grund über
`FilesystemGenerationStore.retention_warning` diagnostizierbar gehalten. Dadurch
können vorübergehend mehr als drei Vorgänger auf der Platte liegen; der gültige
aktive Stand wird für die Aufräumaktion niemals gefährdet. `retry_retention()`
ermöglicht einen kontrollierten weiteren Versuch.

Kundenänderungen werden online sofort mit Basisrevision und Idempotency-Key an
den Server gesendet. Offline werden sie im Overlay angezeigt und in der
Outbox gespeichert. Beim Replay gilt dieselbe Revisionsprüfung. Ein Konflikt
wird zur Entscheidung behalten und niemals automatisch überschrieben.

Jede semantisch neue Kundenänderung erhält einen neuen Idempotency-Key. Geht
eine Antwort nach erfolgreichem Server-Commit verloren, wird zunächst exakt die
alte Anfrage mit ihrem unveränderten Key wiederholt. Erst nach der bestätigten
Antwort wird die nächste lokale Änderung auf die neue Revision umgebogen. Ein
`idempotency_conflict` wird dabei ausdrücklich von einem fachlichen
Revisionskonflikt unterschieden und nicht als vermeintlich zusammenführbarer
Kundenkonflikt angezeigt.

Journaleinträge verwenden dieselbe Offline-Strategie in einer getrennten
Outbox-/Overlay-Komponente: Anlegen, Bearbeiten und Löschen sind offline
sichtbar, werden pro Kunde in Revisionsreihenfolge wiederholt und verändern den
unveränderlichen Kundensnapshot nicht. Ein Konflikt stoppt alle davon
abhängigen Journaländerungen bis zur bewussten Auflösung.

Beim ersten Start werden die kurzzeitig verwendeten Namen
`customer-overlay.db` und `customer-offline-queue.db` verlustfrei in den
kanonischen Outboxpfad übernommen, sofern dort noch keine Daten liegen.

## Pfadmapping

Der Server veröffentlicht `source_id` und relative Pfade. Jeder Client ordnet
die Quelle seinem lokalen Mount zu, beispielsweise:

```text
synology-main -> Z:\Bauvorhaben                 (Windows)
synology-main -> /Volumes/Bauvorhaben           (macOS)
synology-main -> /mnt/synology/Bauvorhaben      (Linux)
```

Aufgelöste Pfade müssen innerhalb des konfigurierten Roots bleiben; absolute
oder aufsteigende Serverpfade werden abgewiesen.

Projektzuordnungen werden im Kundenmodell ausschließlich als `SourcePath`
(`source_id` plus relativer POSIX-Pfad) dargestellt. Alte absolute
`folder_path(s)` können in migrierten Snapshots noch lesbar sein, sind im
Kundeneditor jedoch nicht änderbar und werden bei einer neuen Mutation nicht
zurück an den Server geschrieben. Das gilt gleichermaßen für Laufwerksbuchstaben,
UNC-Pfade sowie absolute macOS-/Linux-Pfade.

## Suche, Ordner und Kundendetails

Die globale lokale Suche vereint Dokumente, Ordner, Projektwurzeln und Kunden,
ohne eine Schreibberechtigung auf den Index zu besitzen. Sie unterstützt
Entitätsfilter, Quelle, Bereich, Jahr, Dateityp und Kunde sowie Sortierung nach
Relevanz, Änderungszeit oder Name und seitenweise Ergebnisse. Facetten werden
direkt aus der aktiven Generation gelesen. Suchbegriffe werden als begrenzte
MRU-Liste atomar in `search-history.json` gespeichert und können lokal gelöscht
werden.

Die Ordnerseite bildet die portablen `folders`- und `project_roots`-Datensätze
der Generation ab. Erst der lokale `SourcePathResolver` übersetzt sie in ein
Windows-, macOS- oder Linux-Ziel. In der Kundenansicht sind Projekte samt
Quelle/Provenienz, Kontakte, Notizen, Tags, Offline-Journal und offene
Dokumentvorschläge getrennt sichtbar.

## Erkennungs-Review und Dokumentvorschläge

Das eigenständige Admin-Tray öffnet einen Review-Dialog für persistente
Erkennungsfälle. Fälle zeigen Projektquellen und Evidenz/Provenienz und können
als neuer Kunde angenommen, einem bestehenden Kunden zugeordnet oder abgelehnt
werden. Ein Erkennungslauf sowie schreibende Entscheidungen benötigen die nur
im Tray-Prozessspeicher gehaltene Adminsitzung. Der normale Hauptclient besitzt
keinen Admin-Control-Adapter und kann daher keinen Adminsitzungstoken erzeugen
oder halten. Annahme und Zuweisung senden
Basisrevision und Idempotency-Key; es gibt kein stilles Überschreiben.

Dokumentbasierte Vorschläge erscheinen am betroffenen Kunden mit Quelldokument,
Regel, Auszug und Konfidenz. Annehmen oder Ablehnen erfolgt explizit über die
revisionierte Client-API mit normalem Clienttoken und eigenem Idempotency-Key;
die heruntergeladene Kundengeneration bleibt unverändert.

## Clientkonfiguration und Onboarding

`client-config.json` speichert Server-URL, Client-API-Token, plattformspezifische
`source_id`-Zuordnungen, das lokale Synchronisationsintervall und das Theme. Der
Client schreibt die Datei atomar.
Beim ersten Lesen werden frühe v1-Feldnamen wie `index_server_url`, `token`,
`source_paths` und `sync_interval_minutes` einmalig in Schema 2 migriert.
Da die Datei den Client-Token im Klartext enthält, wird sie auf POSIX mit Modus
`0600` und auf Windows best-effort über `icacls` ausschließlich für den aktuellen
Benutzer freigegeben. Schlägt die Windows-ACL-Härtung fehl, bleibt das atomare
Speichern standardmäßig möglich, erzeugt aber eine sichtbare
`ClientConfigSecurityWarning`; sicherheitskritische Installationen können mit
`strict_permissions=True` das Speichern stattdessen abbrechen lassen. Als
Alternative kann der Token ausschließlich über `PAPAGUI_API_TOKEN` kommen.

## Native macOS-Prozesse

Das unsigned macOS-Artefakt enthält zwei unabhängige, benachbarte Bundles:
`PapaGUI Client.app` und `PapaGUI Tray.app`. Der Client startet den Tray über
`PapaGUI Tray.app/Contents/MacOS/papagui-tray` im selben Installationsordner.
Beide Apps müssen deshalb gemeinsam installiert oder verschoben werden. Es gibt
keine eingebettete zweite Tray-Kopie; Server und Docker sind in keinem Bundle
enthalten.

Die Einstellungen sind über **Client-Einstellungen** im Hauptfenster erreichbar
und auf die Seiten Verbindung, Pfadzuordnungen sowie Synchronisation/Design
verteilt. Ohne Server-URL oder ohne Pfadzuordnung startet automatisch das
Onboarding. Speichern verdrahtet HTTP-Gateways und `SourcePathResolver` neu und
plant einen bereits laufenden Sync-Timer sofort mit dem neuen Intervall. Zulässig
sind 15 Minuten bis 48 Stunden. Ein Wechsel des Clientdatenordners erfordert
bewusst einen Neustart.

Umgebungsvariablen haben Vorrang vor persistierten Werten. Die Oberfläche zeigt
diese Herkunft durch gesperrte Eingaben mit Hinweistext, sodass ein scheinbar
erfolgloses Überschreiben nicht verborgen bleibt:

- `PAPAGUI_INDEX_SERVER_URL`, `PAPAGUI_API_TOKEN`,
  `PAPAGUI_API_TIMEOUT_SECONDS`
- `PAPAGUI_SOURCE_MAPPINGS` als JSON-Objekt mit `windows`, `macos` und `linux`
- `PAPAGUI_SYNC_INTERVAL_SECONDS`, `PAPAGUI_CLIENT_THEME`
- `PAPAGUI_CLIENT_DATA_ROOT` und optional `PAPAGUI_CLIENT_CONFIG_PATH`

`PAPAGUI_FORCE_FULLSCREEN=1` erhält den bisherigen Vollbildstart. Ohne diese
Variable wird das Hauptfenster normal angezeigt.

## Admin-Tray

Das Tray ist ein eigenständiger Clientprozess. Status und Fortschritt sind mit
dem normalen Clienttoken sichtbar. Änderungen an Servereinstellungen,
Indexaktionen und Neustart verlangen eine Adminanmeldung. Der Sitzungstoken
wird nur im Speicher gehalten und beim kontrollierten Beenden widerrufen.

Die Seite **Indexeinstellungen** bildet alle serverseitigen Felder ab:
automatische Läufe samt Intervall, täglicher Abgleich,
Inhaltsformate/Ausschlüsse und Grenzwerte, OCR- und PDF-Zeitlimits,
Ressourcenprofil, bevorzugte Dokumente/Prioritäten, Sortierreihenfolge und
minimales Kundenjahr. Eingaben werden gegen den gemeinsamen
Vertrag validiert. Laden schützt ungespeicherte Änderungen mit einer Rückfrage;
während Laden oder Speichern sind konkurrierende Aktionen gesperrt und ein
sichtbarer Status meldet Erfolg oder Fehler.

Die Aktivitätsseite liest aktuellen beziehungsweise zuletzt abgeschlossenen
Lauf aus dem regelmäßig abgefragten v2-Serverstatus. Sie zeigt Zeit, Zustand,
Phase, Fortschritt und Fehlermeldung und hält lokal die letzten 50 verschiedenen
Beobachtungen vor.

## Funktionsumfang des 0.4.2-Checkpoints

Die paketnative Oberfläche enthält Dokument-/Globalsuche, portable
Ordner-/Projekt-Navigation, Kundenstammdaten und -details, explizite
Konfliktentscheidungen, Offline-Kunden- und Journal-Outboxes,
Recognition-/Vorschlags-Review, das eigenständige Server-Tray sowie die
persistente grafische Clientkonfiguration. Der Client enthält weiterhin weder
Index-Writer noch Kundenerkennung; diese Verantwortlichkeiten bleiben
ausschließlich auf dem Server.
