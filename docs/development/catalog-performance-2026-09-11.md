# Kataloglauf: Messung vom 11. September 2026

Der untersuchte Engpass liegt im seriellen Schreiben des Katalogs und des
Extraktionscaches. Im Ausgangsprofil entfielen rund 23 von 33 Sekunden auf
`ExtractionStore.put`, darunter knapp 10 Sekunden auf einzelne Cache-Commits.
Weitere rund 8 Sekunden steckten im Katalog-Writer. Die Dokumentleser verwendeten
bereits die vorhandene Parallelisierung.

## Vergleich

Windows, Projekt-Python 3.14.2, Pyinstrument 5.1.3, 1.500 synthetische Textdateien
mit jeweils ungefähr 8 KB Text, 60 Projektverzeichnisse. Der gleiche Benchmark
lief nacheinander mit dem vorherigen und dem optimierten Servercode. Die alten
vier Adapter wurden dafür in einer temporären Paketkopie aus `HEAD` wiederhergestellt;
der Arbeitsbaum blieb unverändert. Die Quellerzeugung und Berichtsausgabe liegen
außerhalb der gemessenen Zeit.

| Kataloglauf | Vorher | Nachher |
| --- | ---: | ---: |
| Nur Metadaten, erster Lauf | 1,03 s | 0,89 s |
| Nur Metadaten, unveränderter Folgelauf | 0,81 s | 0,68 s |
| Mit Textspeicherung, erster Lauf | 32,66 s | 5,48 s |
| Mit Textspeicherung, unveränderter Folgelauf | 1,14 s | 0,88 s |

Der erste Lauf mit Textspeicherung benötigt damit etwa 83 % weniger Zeit und
erreicht ungefähr den sechsfachen Durchsatz. Der reine Metadatenlauf verbessert
sich deutlich weniger. Dies ist eine lokale Einzelmessung mit synthetischen
Daten, keine Zusage für den Kundenbestand, Netzwerkfreigaben oder Docker-Bind-Mounts.
Der komplette Indexjob mit Kundenerkennung und Veröffentlichung wurde nicht
zeitlich vermessen. Der Profiler erfasst den koordinierenden Thread; die Wartezeit
auf Dokumentleser ist enthalten, deren interne Aufrufe sind nicht aufgeschlüsselt.

## Änderungen

- Der Writer baut einmal pro Verbindung eine temporäre indizierte Zuordnung von
  Dokumentpfaden zu FTS-Zeilen auf. Er ersetzt und entfernt Inhalte direkt über
  deren Zeilen-ID. Das verhindert einen vollständigen Durchlauf der
  `UNINDEXED`-Pfadspalte für jede Datei. Bestehende FTS-Zeilen und ihre IDs bleiben
  verwendbar; das veröffentlichte Format ändert sich nicht.
- Der private Extraktionscache zählt seine belegten JSON-Bytes transaktional mit
  SQLite-Triggern. Bestehende Caches werden beim ersten Schreibzugriff innerhalb
  einer Transaktion initialisiert. Größenprüfung, Aktualisierung und Bereinigung
  brauchen danach keine wiederholten Summen über sämtliche Texte.
- Der Cache verwendet während eines Kataloglaufs eine Schreibverbindung pro
  schreibendem Thread. Er wird spätestens vor jedem Katalog-Checkpoint nach
  25 Dateien gespeichert. Ab etwa 1 MiB geschriebener JSON-Nutzdaten erfolgt ein
  früherer Cache-Commit, damit große Dokumente keinen großen Schreibpuffer bilden.
  Cache-Spilling ist für diese begrenzten Schreibabschnitte deaktiviert, damit
  lesende Worker nicht auf einen exklusiven Spill-Lock warten.
- Kontrollierter Abbruch speichert abgeschlossene Cache-Arbeit vor dem
  Katalogzustand. Sonstige Fehler rollen den noch nicht gespeicherten Abschnitt
  zurück. Bereits aktive Kataloge und Backups werden weiterhin erst nach
  erfolgreichem Abschluss ersetzt.
- Die Ordnerklassifikation wird für aufeinanderfolgende Dateien desselben Ordners
  wiederverwendet. Die begrenzte Textqualitätswertung beendet das Buchstabenzählen,
  sobald ihr Höchstwert erreicht ist.

Die Verwendung von FTS-Zeilen-IDs und transaktionalen Triggern folgt den
[SQLite-FTS5-Funktionen](https://www.sqlite.org/fts5.html) und der
[SQLite-Trigger-Semantik](https://www.sqlite.org/lang_createtrigger.html).

## Prüfumfang und Wiederholung

Regressionen prüfen direkte FTS-Löschung mit alten, nicht zusammenhängenden
Zeilen-IDs, Aktualisierung bestehender Caches, Bytezählung mehrerer Writer,
Größenlimits, Bereinigung, Rollback, kontrollierten Abbruch, dauerhafte
Cache-Verweise vor Katalog-Checkpoints und paralleles Lesen großer Artefakte.
Die bestehenden Serverprüfungen decken außerdem inkrementelle Änderungen,
Dokumentausschlüsse, Wiederaufnahme und atomare Generationen ab.

Abnahme: Die Server-/Vertragssuite bestand mit 574 erfolgreichen Tests und
43 erfolgreichen Subtests; drei plattformspezifische Fälle wurden unter Windows
übersprungen. Die anschließend um die Checkpoint-Prüfung ergänzte Datei
`test_catalog_write_performance.py` bestand separat mit allen sechs Tests.
Ruff, Abhängigkeitsprüfung und Graphify-Prüfung waren ebenfalls erfolgreich.

```powershell
.\.venv\Scripts\python.exe tools/profile_catalog.py --documents 1500 --output profiles/catalog.json
.\.venv\Scripts\python.exe -m pytest tests/server tests/contracts -q
```

Die Messartefakte liegen lokal und Git-ignoriert unter `profiles/catalog-baseline*`
und `profiles/catalog-optimized*`. Der nächste Start über `start.bat` baut den
geänderten Servercode ins Docker-Image ein; ein bereits laufender Container
übernimmt Quelländerungen nicht automatisch.

## Nachlauf nach abgeschlossenem Dateiscan

Die Untersuchung eines scheinbar bei 5.153 Dateien stehenden Jobs zeigte:
Der Katalog war fertig, die Kundenerkennung lief weiter. Aggregierte lokale
Statusabfragen belegten zunächst 42, später 69 abgeschlossene Kundenprüfungen
von 80 Kunden. Die API meldete währenddessen unverändert den Dateizähler des
Kataloglaufs.

`SqliteCatalogReader.document_evidence()` verwendete eine Rangfolgen-CTE als
Coroutine und verband sie anschließend erneut über die nicht indizierte
FTS-Pfadspalte. Der SQLite-Abfrageplan konnte dadurch die gesamte Rangfolge
für jede äußere FTS-Zeile wiederholen. Die CTE wird jetzt ausdrücklich
materialisiert; der zweite Zugriff verwendet die tatsächliche FTS-Zeilen-ID.
Projektfilter, Rangfolge, Seiteneinteilung und alte Kataloge bleiben unterstützt.

Bei einer isolierten, rein lesenden Abfrage der größten Projektgruppe im lokalen
Docker-Bestand war die bisherige Abfrage nach über zwei Minuten noch nicht fertig
und wurde beendet. Dieselbe Auswahl mit der korrigierten Abfrage lieferte dreimal
24 Dokumente in 0,762 / 0,761 / 0,688 Sekunden. Der Bestand umfasste 1.598
Volltextzeilen mit insgesamt rund 14,1 Millionen Zeichen. Nur aggregierte Zahlen
wurden ausgegeben; Dokumentinhalte und Kundennamen wurden nicht übernommen.
Diese Messung betrifft die SQL-Abfrage ohne Laden strukturierter Cache-Artefakte,
nicht die Gesamtdauer des Indexjobs. Beide Messungen fanden neben dem laufenden
Indexjob statt; die abgebrochene Ausgangsmessung liefert keine genaue Laufzeit.

Regressionstests begrenzen die ausgeführten SQLite-Schritte mit einem
Progress-Handler. Sie prüfen aktuelle und ältere Kataloge, abweichende FTS-IDs,
fehlende Texte und Seitenwechsel. Die ursprüngliche Abfrage überschreitet in
beiden Varianten das Budget; die korrigierte Abfrage besteht. Zusätzlich prüfen
Tests die Weitergabe von Kunden- und Dokumentzählern bis in die GUI sowie
Statusabfragen während einer angehaltenen Archivveröffentlichung.

Die Status-API verwendet während der Erkennung `processed_items`/`total_items`
für Kundengruppen bzw. Kunden. `catalog_processed_items` hält den abgeschlossenen
Dateizähler fest, `evaluated_documents` zeigt die laufende Dokumentprüfung.
Die Phasen heißen `customer-recognition`, `customer-documents` und `publishing`.
Fortschrittsmeldungen benötigen keine zusätzliche Transaktion pro Dokument.
Die Aufbewahrungsdiagnose verwendet einen kurzen eigenen Lock, damit sie während
der Veröffentlichung nicht auf Kopieren und Komprimieren warten muss.

Abnahme dieses Nachlaufs: 595 Tests und 43 Subtests erfolgreich, drei
plattformspezifische Tests übersprungen (Server, Verträge und GUI-Lebenszyklus).
Die beiden SQL-Regressionstests bestanden zusätzlich im gebauten Docker-Image
mit Python 3.11.16 und SQLite 3.46.1, isoliert ohne Netzwerk und Kundendaten-Mounts.
Der HTTP-Test deckt die neuen Zähler im strikten Antwortschema in beiden
Erkennungsphasen und bei der Veröffentlichung ab; der OpenAPI-Snapshot ist
aktualisiert. Ein erster Lauf im lokalen Bestand nach Einbau der SQL-Korrektur
schloss die gesamte Kundenerkennung für 80 Kunden in rund 72 Sekunden ab
(20:09:09 bis 20:10:21 UTC) und veröffentlichte anschließend erfolgreich.
Das ist eine Betriebsbeobachtung mit vorhandenen Caches, kein kontrollierter
Vergleich der gesamten Indexlaufzeit. Das vervollständigte Image wurde danach
mit unveränderten Daten-Mounts eingesetzt.
