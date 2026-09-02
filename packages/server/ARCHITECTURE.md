# PapaGUI-Server: Indexlauf und Ressourcenmodell

Der Server besitzt genau einen schreibenden `IndexRunCoordinator`. Ein Lauf scannt
die gemountete Quelle vollständig, verwendet aber Datei-Metadaten für inkrementelle
Arbeit: unveränderte Dateien werden nicht erneut extrahiert. Ein abgebrochener Lauf
bleibt als `builds/resume.db` erhalten und wird beim nächsten inkrementellen Lauf
fortgesetzt. Erst nach `PRAGMA integrity_check` wird der Katalog atomar aktiviert.
Fehler und Abbrüche rotieren daher weder den aktiven Stand noch Backups.

Vor jedem Lauf prüft `PersistentSourceIdentityGuard` den Mount gegen eine im
Configvolume gespeicherte, portable Quellidentität. Ein vorhandener, aber leerer
Mountpoint sowie ein Mount ohne mindestens einen bekannten Wurzeleintrag gelten
als nicht verfügbar. Das verhindert die gefährliche Interpretation „NAS fehlt =
alle Dateien wurden gelöscht“. Die Erstinitialisierung ist konfigurierbar.

PDF-Text wird zuerst mit `pdftotext` gelesen. Bei zu wenig eingebettetem Text kann
eine begrenzte Tesseract-OCR folgen. `ExtractionResourcePolicy` begrenzt pro Dokument
Seitenzahl, Auflösung, Laufzeit und Pausen; externe Programme werden als
Argumentliste ohne Shell gestartet. OCR-Ergebnisse fließen in den FTS-Katalog.
Der Katalog speichert ausschließlich `source_id` plus relativen POSIX-Pfad und
normalisiert `folders` und `project_roots`. Eindeutige Projektwurzeln werden einem
Kunden zugeordnet. Ähnliche Namen, doppelte Treffer und verschiedene Städte werden
als persistente Review-Fälle gespeichert, niemals still zusammengeführt. E-Mail-,
Telefon- und migrierte Kontaktfunde aus Dokumenten werden als prüfbare Vorschläge
gespeichert und überschreiben keine Stammdaten.

Ein vollständiger Lauf staged Index- und Kundenarchiv, schreibt beide
Komponentenpointer und aktiviert sie anschließend mit genau einem atomaren Austausch
von `active-generation.json`. Schlägt eine Stufe vorher fehl, werden beide Stages
verworfen und die vorherigen Pointer wiederhergestellt. Aufräumen nach dem Commit ist
best effort und kann einen gültig aktivierten Stand nicht nachträglich fehlschlagen
lassen. Je Komponente bleiben der aktive Stand plus drei Vorgänger erhalten.

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
