# Desktop-Client

Die serverweite Sperrliste und den vollständigen Neuaufbau findest du unter
**Indexserver → Tab „Kundenerkennung“**. Beide Funktionen arbeiten auf dem
Server. Eingaben bleiben beim Tabwechsel erhalten; Statusabfragen pausieren in
ausgeblendeten Tabs. Der Neuaufbau läuft nach dem Schließen des Fensters weiter;
Status und Abbruch sind beim erneuten Öffnen verfügbar. Näheres beschreibt die
[Verwaltung der Kundenerkennung](kundendatenerkennung.md#serverweite-sperrliste-und-vollständiger-neuaufbau).

## Verantwortung

Der Desktop-Client zeigt und bearbeitet Kunden, durchsucht Kunden und Ordner im
lokalen Katalog und synchronisiert Snapshots mit dem Server. Er baut selbst
keinen Index auf und schreibt niemals in eine heruntergeladene Generation.

Die sichtbare Oberfläche verwendet das Karten- und Navigationsdesign von
v0.4.1 mit ergänzten Bedienflächen für die aktuellen Funktionen. Diese
Präsentationsschicht ist über die
Client-Application-Ports mit der getrennten 0.4.2-Architektur verbunden. Eine
detaillierte Zuordnung der wiederhergestellten Ansichten und der noch
ausstehenden neuen Bedienoberflächen steht unter
[Wiederherstellung des v0.4.1-GUI-Designs](gui-design-restoration.md).

Im Kundeneditor lässt sich die Kundenart über das gesamte Dropdown-Feld „Art“
auswählen: Unternehmen, Privatperson oder Organisation. Abweichende Werte aus
vorhandenen Kundendaten werden zusätzlich angeboten und beim Speichern erhalten.

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
Outbox-/Overlay-Komponente innerhalb derselben `customer-outbox.db`:
Anlegen, Bearbeiten und Löschen sind offline
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

Die sichtbare globale Suche ist vorerst auf Kunden und Projektordner begrenzt,
ohne eine Schreibberechtigung auf den Index zu besitzen. Strukturierte
Unterordner können im Filter ausdrücklich zugeschaltet werden; einzelne Dateien
und extrahierte Dateiinhalte werden nicht als Suchtreffer dargestellt. Bereich,
Jahr und Sortierung bleiben verfügbar. Die Ergebnisanzahl ist begrenzt, damit
Qt nicht hunderte komplexe Ergebniskarten gleichzeitig im GUI-Thread erzeugen
muss. Suchbegriffe werden als begrenzte MRU-Liste atomar in
`search-history.json` gespeichert und können lokal gelöscht werden.

Die Ordnerseite bildet die portablen `folders`- und `project_roots`-Datensätze
der Generation ab. Unterordner werden bedarfsgerecht beim Aufklappen aus der
lokalen Generation gelesen, sodass ihre direkten Dateien und weiteren
Unterordner einsehbar sind. Erst der lokale `SourcePathResolver` übersetzt sie
in ein Windows-, macOS- oder Linux-Ziel. In der Kundenansicht sind Projekte samt
Quelle/Provenienz, Kontakte, Notizen, Tags, Offline-Journal und offene
Dokumentvorschläge getrennt sichtbar.

## Erkennungs-Review und Dokumentvorschläge

Das eigenständige Admin-Tray öffnet einen Review-Dialog für persistente
Erkennungsfälle. Fälle zeigen Projektquellen und Evidenz/Provenienz und können
als neuer Kunde angenommen, einem bestehenden Kunden zugeordnet oder abgelehnt
werden. Erkennungsläufe und Entscheidungen verwenden den konfigurierten
Client-API-Token; eine zusätzliche Adminanmeldung oder Adminsitzung gibt es
nicht. Der normale Hauptclient besitzt keinen Server-Control-Adapter und öffnet
für die serverweite Verwaltung den unabhängigen Tray-Prozess. Annahme und
Zuweisung senden
Basisrevision und Idempotency-Key; es gibt kein stilles Überschreiben.

Dokumentbasierte Vorschläge erscheinen am betroffenen Kunden mit Quelldokument,
Regel, Auszug und Konfidenz. Annehmen oder Ablehnen erfolgt explizit über die
revisionierte Client-API mit normalem Clienttoken und eigenem Idempotency-Key;
die heruntergeladene Kundengeneration bleibt unverändert.

Die Kundenprüfung gruppiert gleiche Informationen und ihre Belege, zeigt
Konflikte und Abdeckung und lädt weitere Vorschläge seitenweise. **Neu bewerten**
prüft bereits ausgelesene Texte für diesen Kunden erneut; **Neu auslesen**
fordert zusätzlich die erneute Dokumentauslesung einschließlich OCR auf dem
Server an. Offline bleibt der vorhandene Vorschlagsstand lesbar; Entscheidungen
und neue Erkennungsaufträge benötigen die Serververbindung.

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

## Plattformen und Start

Client und Tray verwenden Qt/PySide6 und portable Quellzuordnungen für Windows,
macOS und Linux. Die installierten Einstiegspunkte heißen `papagui-client` und
`papagui-tray`; der Server ist ein getrenntes Paket beziehungsweise ein
Docker-Dienst. Das Öffnen des Indexserver-Fensters startet ausschließlich den
Tray-Prozess.

Der Entwicklungsstarter [`start.ps1`](../start.ps1) verwendet unter Windows
Python aus `.venv` oder `venv`, setzt die Clientverbindung und startet Tray und
Hauptclient. Er kann zusätzlich den getrennten Docker-Server starten;
`PAPAGUI_SKIP_DOCKER=1` unterbindet diesen Schritt. Bei fehlendem Server bleibt
der zuletzt synchronisierte lokale Stand nutzbar. Token werden per Python
erzeugt, Secretdateien per numerischer Windows-Benutzer-SID abgesichert;
ACL-Fehler erzeugen einen Warnhinweis.

Die Startskripte und die Qt-Oberfläche sind mit synthetischen Tests abgesichert.
Das ersetzt keine native End-to-End-Abnahme auf Windows, macOS und Linux;
insbesondere wurde der korrigierte Windows-Start hier nicht auf einem nativen
Windows-System abgenommen.

### Native macOS-Prozesse

Das unsigned macOS-Artefakt enthält zwei unabhängige, benachbarte Bundles:
`PapaGUI Client.app` und `PapaGUI Tray.app`. Der Client startet den Tray über
`PapaGUI Tray.app/Contents/MacOS/papagui-tray` im selben Installationsordner.
Beide Apps müssen deshalb gemeinsam installiert oder verschoben werden. Es gibt
keine eingebettete zweite Tray-Kopie; Server und Docker sind in keinem Bundle
enthalten.

## Einstellungen im Hauptclient

Die Einstellungen sind über die Zahnrad-Schaltfläche der alten Kopfzeile
erreichbar. Das wiederhergestellte Popup verwendet die Bereiche **Allgemein**,
**Indexierung**, **Suche**, **Kundenerkennung**, **Statistik** und **Aussehen**;
Serververbindung, Pfadzuordnungen und lokale Synchronisation sind in dieses
Raster eingeordnet. Die Statistik-Seite zeigt wieder die aus der lokalen
Kundenkopie und dem aktiven Suchkatalog ermittelten Kennzahlen; darunter bleibt
die 0.4.2-Zusammenfassung der Clientkonfiguration sichtbar. **Indexserver
öffnen** startet ausschließlich den unabhängigen Tray-Prozess und führt keinen
Indexcode im Client aus. Ohne Server-URL oder ohne Pfadzuordnung startet
automatisch das Onboarding. Speichern verdrahtet HTTP-Gateways und
`SourcePathResolver` neu und plant einen bereits laufenden Sync-Timer sofort mit
dem neuen Intervall. Zulässig sind 15 Minuten bis 48 Stunden. Ein Wechsel des
Clientdatenordners erfordert bewusst einen Neustart.

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

Das Fenster enthält die Tabs **Übersicht**, **Indexeinstellungen**,
**Kundenerkennung** und **Aktivität**. Der Tab **Kundenerkennung** bleibt auch bei
einem Verbindungsfehler erreichbar. **Aktualisieren** lädt seinen Serverstand
erneut; schreibende Aktionen sind bei fehlender Verbindung gesperrt.

Im Tab **Kundenerkennung** sammelt **Sperre vormerken** mehrere Ausschlüsse
zunächst lokal. Neue Sperren und vorgemerkte Entfernungen sind in der Tabelle
als unbestätigt markiert. Mehrere Zeilen lassen sich gemeinsam auswählen;
**Auswahl entfernen** merkt gespeicherte Sperren zur Entfernung vor oder nimmt
neue Vormerkungen zurück. Eine vorgemerkte Entfernung lässt sich zurücknehmen.

**Änderungen bestätigen** übermittelt alle Vormerkungen gemeinsam. Der Server
prüft bei einer tatsächlichen Änderung die vorhandenen Vorschläge einmal und
veröffentlicht einen Kundenstand;
eine erneute Dokumentauslesung wird dadurch nicht gestartet. **Vormerkungen
verwerfen** nimmt die lokalen Änderungen zurück. Laden, Tabwechsel und das
Ausblenden des Fensters erhalten Vormerkungen. Bei einem Speicherfehler bleiben
sie für die Korrektur oder einen erneuten Versuch stehen. Der vollständige
Neuaufbau ist eine separate Aktion und wartet auf Bestätigen oder Verwerfen.
Es lassen sich höchstens 500 neue Sperren und 500 Entfernungen pro Bestätigung
sammeln. Die Vormerkungen liegen im Speicher des Tray-Prozesses; nach dessen
Beendigung oder Neustart sind unbestätigte Änderungen nicht erhalten.
Sie gehören nicht zur dauerhaften Offline-Outbox für Kunden und Journal.

Falls die Sperrliste gespeichert wurde, ihre Veröffentlichung aber scheitert,
zeigt die Oberfläche diesen Zustand ausdrücklich an. **Veröffentlichung
wiederholen** wiederholt nur den noch ausstehenden Schritt. Ältere Server ohne
die neue Stapelschnittstelle benötigen ein Update; der Client weicht nicht auf
viele einzelne Schreibanfragen aus.
Der Server meldet eine ausstehende Veröffentlichung auch nach einem Neustart
des Clients, damit die Wiederholung weiterhin erreichbar bleibt. Bis zur
erfolgreichen Veröffentlichung bleibt der vollständige Neuaufbau gesperrt.
Die Bestätigung verwendet genau eine Batch-Anfrage; für die anschließende
Veröffentlichung gilt ein HTTP-Zeitlimit von mindestens 60 Sekunden, ohne die
Oberfläche zu blockieren. Statusabfragen behalten ihre normale Wartezeit.

Das Tray ist ein eigenständiger Clientprozess. Status und Fortschritt sind mit
dem konfigurierten Clienttoken sichtbar. Derselbe Token autorisiert Änderungen
an Servereinstellungen, Sperrliste, Indexaktionen und Neustart; es gibt keinen
zusätzlichen Passwortdialog oder separaten Adminsitzungstoken.

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

Die Dokumentkarte zeigt die tatsächlich aktiven Worker und ihre Obergrenze,
die Warteschlange sowie gefundene, verarbeitete, wiederverwendete, extrahierte
und fehlerhafte Dokumente. Diese Werte werden bereits während der Katalogphase
aktualisiert. Beim vollständigen Neuaufbau erscheinen sie auch im Tab
**Kundenerkennung**, solange die Dokumentauslesung läuft. Ältere Server bleiben
bedienbar; die Übersicht weist auf fehlende Workerstatistiken hin, während der
Neuaufbau weiterhin seinen allgemeinen Jobstatus zeigt. Die Workeranzeige
enthält ausschließlich Summen und keine Dokument- oder Kundenzuordnung.

Das Ressourcenprofil berechnet die Parallelität aus den verfügbaren CPU- und
RAM-Ressourcen des Servers einschließlich Containergrenzen: **Schonend** nutzt
15 %, **Ausgewogen** 25 % und **Schnell** 60 % der verfügbaren CPU-Kapazität
für die Workerberechnung. Speicherreserve und Dokumentlimits können die Zahl
weiter senken; zulässig sind 1 bis 20 Worker. Der Tooltip erläutert das Budget,
die Dokumentkarte zeigt die für den laufenden Auftrag ermittelte Grenze.

Reguläre Läufe überspringen bereits verarbeitete Dokumente bei unveränderter
Dateigröße, Änderungszeit und Auslesekonfiguration ohne erneutes Lesen der
Quelldatei. Der aktivierte tägliche Abgleich prüft nach 24 Stunden die
Inhaltshashes; auch ein ausdrücklicher Indexneuaufbau prüft die Inhalte.
Vorhandene passende Extraktionsergebnisse können dabei wiederverwendet werden.
**Kundenerkennung vollständig neu aufbauen** erzwingt zusätzlich die erneute
Dokumentauslesung.

## Aktueller Funktionsumfang

Die paketnative Oberfläche enthält Kunden-/Ordnersuche und Dokumentvorschauen,
portable
Ordner-/Projekt-Navigation, Kundenstammdaten und -details, explizite
Konfliktentscheidungen, Offline-Kunden- und Journal-Outboxes,
Recognition-/Vorschlags-Review, das eigenständige Server-Tray sowie die
persistente grafische Clientkonfiguration. Der Client enthält weiterhin weder
Index-Writer noch Kundenerkennung; diese Verantwortlichkeiten bleiben
ausschließlich auf dem Server.

Der nachträgliche Design-Rückbau ersetzt keine dieser Funktionen. Er stellt
die v0.4.1-Interaktion wieder her und führt die neuen 0.4.2-Funktionen dahinter
über Ports, Gateways und Presentermodelle aus.

Die Kundendatenprüfung bündelt gleiche Informationen mit ihren Belegen, zeigt Konflikte und Teilabdeckung und kann einzelne Kunden serverseitig erneut prüfen. Bedienung, API und Betrieb sind in [Kundendatenerkennung](kundendatenerkennung.md) beschrieben.

Die Vorschlagsprüfung passt Karten und Texte an die Dialogbreite an. Belege sind zunächst eingeklappt; Annehmen und Ablehnen stehen vor den Fundstellen. Quellen zeigen einen gekürzten Dateinamen, den unveränderten vollständigen Pfad im Tooltip und die Fundstelle im aufgeklappten Bereich. Der Dialog passt beim Öffnen auf den Bildschirm und lässt sich vergrößern.

[Vorschau der Kundenprüfung mit ausschließlich erfundenen Testdaten](assets/customer-review-synthetic.png).
