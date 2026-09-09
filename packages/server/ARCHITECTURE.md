# PapaGUI-Server: Indexlauf und Ressourcenmodell

Der Server besitzt einen `IndexRunCoordinator` und eine gemeinsame
`operation_lock` für Indexläufe und Kundenprüfaufträge. Innerhalb eines Laufs
lesen begrenzte Dokumentworker parallel; ein einzelner Schreiber übernimmt
Katalog- und Cacheergebnisse. Die fachliche Erkennung und Zuordnung der
Kunden bleibt anschließend sequenziell. Kundenänderungen und Sperrlisten
verwenden eigene kurze SQLite-Transaktionen und müssen nicht auf die gesamte
Dokumentauslesung warten.

Ein Indexlauf scannt die gemountete Quelle vollständig und entfernt nicht mehr
vorhandene Pfade. Bei regulären Folgeläufen werden erfolgreich verarbeitete
Dateien mit unveränderter Größe, Änderungszeit und Extraktionskonfiguration
ohne erneutes Öffnen der Quelldatei übernommen. Eine Inhaltsprüfung liest
zusätzlich die Hashes der zugelassenen Dokumente. Passende Cacheergebnisse
verhindern dabei erneute Parser-/OCR-Arbeit.

Ein abgebrochener Kataloglauf bleibt unter
`index/catalog/builds/resume.db` mit Wiederaufnahmeinformationen erhalten.
Abgeschlossene Dokumentergebnisse werden alle 25 Dateien gesichert. Die
Wiederaufnahme prüft Quell- und Konfigurationskompatibilität. Erst nach
`PRAGMA integrity_check` wird der neue Arbeitskatalog atomar aktiviert.
Eine fehlgeschlagene oder abgebrochene Katalogerstellung ersetzt den zuvor
aktiven Katalog nicht. Die spätere Veröffentlichung für Clients besitzt
eine eigene Transaktionsgrenze.

Vor jedem Lauf prüft `PersistentSourceIdentityGuard` den Mount gegen eine im
Configvolume gespeicherte, portable Quellidentität. Ein vorhandener, aber leerer
Mountpoint sowie ein Mount ohne mindestens einen bekannten Wurzeleintrag gelten
als nicht verfügbar. Das verhindert die gefährliche Interpretation „NAS fehlt =
alle Dateien wurden gelöscht“. Die Erstinitialisierung ist konfigurierbar.

PDF-Text und Layout werden zuerst durch `pdfplumber` in einem isolierten
Parserprozess gelesen. `pdftotext` ist der Rückfallpfad bei fehlendem oder
fehlerhaftem Layoutparser. Seiten mit unzureichendem Text werden bei aktivierter
OCR begrenzt mit Tesseract gelesen; Wortpositionen und Qualitätsangaben bleiben
erhalten. DOCX, XLS/XLSX, DOC, Textdateien und unterstützte Bilder verwenden
dieselbe strukturierte Extraktionsschnittstelle. `ExtractionResourcePolicy`
begrenzt pro Dokument Seitenzahl, Auflösung, Laufzeit und Pausen; externe
Programme werden als Argumentliste ohne Shell gestartet. OCR-Ergebnisse fließen
in den FTS-Katalog.
Der Katalog speichert ausschließlich `source_id` plus relativen POSIX-Pfad und
normalisiert `folders` und `project_roots`. Eindeutige Projektwurzeln werden einem
Kunden zugeordnet. Ähnliche Namen, doppelte Treffer und verschiedene Städte werden
als persistente Review-Fälle gespeichert, niemals still zusammengeführt. E-Mail-,
Telefon- und migrierte Kontaktfunde aus Dokumenten werden als prüfbare Vorschläge
gespeichert und überschreiben keine Stammdaten.

## Worker, Cache und Abbruch

`document_worker_budget` berechnet die Obergrenze pro Lauf anhand effektiver
CPU-Kapazität, CPU-Affinität und cgroup-Grenzen. `gentle`, `balanced` und `fast`
verwenden 15 %, 25 % beziehungsweise 60 % der CPU-Kapazität für diese Berechnung.
Zusätzlich begrenzen verfügbarer RAM, mindestens 512 MiB beziehungsweise 15 %
Reserve und der geschätzte Speicherbedarf je Dokument die Zahl auf 1–20 Worker.
Unbekannter oder knapper RAM erlaubt nur einen Worker. Das Profil ist keine
Garantie einer festen CPU-Auslastungsgrenze.

`ParallelDocuments` hält höchstens das Doppelte der Workerzahl als
ausstehende Aufgaben. Identische Inhaltshashes mit gleicher Parser-/Werkzeug-
und Einstellungsversion teilen ohne erzwungene Auslesung ein Ergebnis im
begrenzten Laufpuffer. Bei `force_extraction` wird jeder Dokumentpfad erneut
ausgelesen. Der dauerhafte
private Extraktionsstore liegt unter `extraction/artifacts.db`; große Layout-
und OCR-Artefakte werden nicht in die Clientgenerationen aufgenommen. Aktive
und aufbewahrte Katalog-/Generationsreferenzen schützen Artefakte beim Aufräumen.
Volltextsuche, Kurzbelege und Entscheidungen verwenden die entsprechenden
Index- und Kundensnapshots.

Zeit-, Seiten-, Zeichen-, Dateigrößen- und Bildgrenzen gelten pro Dokument.
Isolierte Office-/PDF-Parser erhalten auf Linux zusätzlich ein Speicherlimit;
OCR läuft mit einem OpenMP-Thread. Ein Abbruch stoppt weitere Einreichungen,
wartende Aufgaben und überwachte Parser-/OCR-Prozesse; auf POSIX wird dabei
deren Prozessgruppe beendet. In laufenden Dateileseschleifen wird der Abbruch
an Verarbeitungsgrenzen geprüft. Die bisherigen aktiven Clientgenerationen
bleiben erhalten.

`DocumentWorkStatus` liefert nur aggregierte Worker-, Warteschlangen-, Lese-,
Cache-, Extraktions- und Fehlerzähler sowie die Laufzeit. Die optionale
Fähigkeit `document-workers` macht diese unter `/v2/server/status` und bei
laufenden auslesenden Kunden-/Neuaufbauaufträgen verfügbar.

## Zeitplanung und Prüfmodi

Das automatische Intervall ist standardmäßig 24 Stunden und zwischen
15 Minuten und 48 Stunden einstellbar. Bei aktivierter täglicher
Inhaltsprüfung bestimmt zusätzlich der im aktiven Katalog gespeicherte
`last_content_verification_at` den nächsten Termin nach 24 Stunden. Auch bei
48 Stunden normalem Intervall bleibt beispielsweise eine vor 23 Stunden
ausgeführte Inhaltsprüfung in einer Stunde fällig. Nur ein vollständig
abgearbeiteter, aktivierter Kataloglauf für alle Projekte aktualisiert diesen
Zeitpunkt; eine einzelne Kundenprüfung tut dies nicht. Teilweise lesbare
Dokumente behalten dabei ihren jeweiligen Abdeckungsstatus.

`serve` fordert standardmäßig einen Startlauf an. `--no-run-on-start`
unterdrückt diesen: eine bereits überfällige oder noch nie ausgeführte
Inhaltsprüfung wartet dann bis zum kleineren Wert aus Intervall und
24 Stunden. Ein noch bevorstehender gespeicherter Prüftermin bleibt wirksam.
`automatic_runs_enabled=false` verhindert automatische Termine; ausdrücklich
angeforderte Läufe bleiben möglich. `daily_reconciliation_enabled=false`
deaktiviert die zusätzliche Inhaltsprüfung. Fehlgeschlagene oder abgebrochene
überfällige Prüfungen werden mit begrenztem Intervall erneut versucht und
erzeugen keine Schleife ohne Wartezeit.

Ein ausdrücklicher Indexneuaufbau (`full_rebuild`) erstellt den Katalog neu
und prüft Inhalte, verwendet aber passende Extraktionscacheeinträge weiter.
`reassess` bewertet vorhandene Texte, `extract` erzwingt die Auslesung für
zugeordnete Kundenprojekte. Ein globaler Erkennungsneuaufbau (`rebuild`)
erzwingt Katalog- und Dokumentneuaufbereitung und bewertet alle Kunden über
das gewöhnliche Projektsuchbudget hinaus. Dateifilter und Schutzgrenzen
bleiben in allen Modi wirksam.

## Speicherung und Veröffentlichung

Ein vollständiger Lauf bereitet Index- und Kundenarchiv vor und aktiviert beide
Komponentenpointer mit genau einem atomaren Austausch von `active-generation.json`.
Schlägt die Vorbereitung oder Aktivierung fehl, werden die vorbereiteten
Artefakte verworfen und die bisherigen veröffentlichten Pointer bleiben aktiv.
Ein zuvor fertiggestellter Arbeitskatalog oder bereits gespeicherte
Kundendaten sind davon getrennt. Aufräumen nach dem Commit ist
best effort und kann einen gültig aktivierten Stand nicht nachträglich fehlschlagen
lassen. Je Komponente bleiben der aktive Stand plus drei Vorgänger erhalten.

Globale Sperren liegen in `recognition_blocklist`. Eine Stapeländerung wird
vollständig validiert und unter `BEGIN IMMEDIATE` als Ergänzungs-/Löschdelta
gespeichert. Ein einzelner Abgleich setzt passende offene Vorschläge auf
`blocked` oder stellt weiterhin belegte Vorschläge wieder her; frühere
Entscheidungen, Belege und Stammdaten bleiben erhalten. Monoton vergebene IDs
verhindern, dass wiederholte Löschanfragen neue Einträge treffen.

Nach einem geänderten Stapel veröffentlicht der Dienst einmal die
Kundenkomponente. Es wird keine Dokumentauslesung ausgelöst. Ein dauerhaftes
Veröffentlichungstoken in `candidate_schema_metadata` wird nach Erfolg nur
gelöscht, wenn es noch zum veröffentlichten Änderungsstand gehört. Bei einem
Fehler bleiben Änderungen gespeichert und die API liefert `published: false`.
`GET` zeigt `publication_pending`; ein leerer Stapel wiederholt den offenen
Schritt auch nach einem Neustart. Ohne Änderung und ohne offenen Schritt
entsteht keine Veröffentlichung.

## Warum derzeit keine Shards

Der v2-Katalog ist ein einzelnes SQLite/FTS-Artefakt. Das erlaubt eine einfache,
atomare Generation, konsistente Prüfsummen und zuverlässige Offlinekopien. Die
frühere Shard-Pipeline würde mehrere partielle Zustände und einen zusätzlichen
Merge-/Recovery-Vertrag benötigen, ohne dass für die aktuelle Datenmenge ein
gemessener Engpass vorliegt. Sharding und eine entsprechende Scheinoption sind daher
bewusst kein Bestandteil von 0.4.2. Es wird erst eingeführt, wenn Benchmarks einen
Bedarf belegen; dann hinter dem
`CatalogIndexerPort`, ohne API- oder Clientänderung.

## Warum kein Dateisystem-Watchdog

SMB/NAS-Mounts liefern plattformübergreifend keine garantiert vollständigen oder
geordneten Dateisystemereignisse. Ein Watchdog wäre deshalb keine verlässliche
Wahrheitsquelle. Der Scheduler führt stattdessen regelmäßige Vollabgleiche durch;
der Scanner verarbeitet dabei über Größe und Änderungszeit nur geänderte Dateien
und entfernt verschwundene Pfade. Damit ist ein Lauf logisch vollständig, praktisch
inkrementell und korrigiert auch verpasste NAS-Ereignisse. Manuelle Vollaufbauten
bleiben für Schemawechsel und Reparatur verfügbar.
