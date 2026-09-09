# Parallele Dokumentverarbeitung – Prüfung vom 9. September 2026

> Historischer Prüfbericht, Dokumentationsabgleich am 9. September 2026.
> Testzahlen, Coverage und Messwerte beschreiben die jeweils unten genannte
> Entwicklungsstufe. Spätere Änderungen an Batch-Sperrliste, Zeitplanung und
> Windows-Start sind damit nicht automatisch erneut abgenommen. Den aktuellen
> Funktionsstand und offene Abnahmen beschreibt die
> [Umsetzungsübersicht](../Umsetzungsplanung.md); reproduzierbare Prüfwege stehen
> in der [Entwicklungsdokumentation](development.md).


## Umsetzung

- Indexierung und Kundenerkennung verwenden dieselbe parallele Dokumentauslesung.
  Die anschließende fachliche Kundenzuordnung läuft weiterhin nacheinander.
- CPU, Ressourcenprofil, verfügbarer RAM und Containergrenzen bestimmen 1–20
  Worker. Die Warteschlange ist begrenzt; Katalog und Cache haben einen Schreiber.
- Reguläre Folgeläufe überspringen unveränderte Dateien anhand von Änderungszeit,
  Größe und Auslesekonfiguration. Beim ersten Lauf nach dem Update werden die
  zusätzlichen Vergleichsmetadaten aufgebaut.
- Der aktivierte tägliche Abgleich und ausdrückliche Indexneuaufbauten prüfen
  Inhaltshashes. Die tägliche Frist überlebt Neustarts und gilt auch bei längeren
  Indexintervallen. Deaktivierte automatische Läufe bleiben deaktiviert.
- Serverstatus und Oberfläche zeigen aktive Worker, Obergrenze, Warteschlange und
  Dokumentzähler. Ältere Server bleiben kompatibel.

## Prüfung

882 Tests in 79 Modulen bestanden, einschließlich gezielter Nachtests nach den
letzten Korrekturen. Gesamtabdeckung: 95 % bei geforderten 93 %. Ruff und
Diff-Prüfung bestanden; der OpenAPI-Vertrag wurde aktualisiert und geprüft.

Die Regressionen prüfen Parallelität, Warteschlangengrenzen, Cache-Konsistenz
bei veränderten identischen Dateien, Speicherfehler, Parserfehler, Abbruch vor
Veröffentlichung, Wiederaufnahme abgeschlossener Dokumente und den täglichen
Zeitplan. Parser- und OCR-Unterprozesse können beim Abbruch beendet werden.

## Synthetischer Laufzeitvergleich

| 24 erzeugte Dokumente | 1 Worker | 4 Worker |
|---|---:|---:|
| DOCX mit tatsächlichem Parser (Abschlusslauf) | 1,5139 s | 0,4969 s |
| Kontrollierte Auslesedauer von je 40 ms | 0,9915 s | 0,2546 s |

Alle unveränderten Folgeläufe öffneten null Quelldateien und starteten null
Extraktionen. Reproduzierbar mit:

```bash
.venv/bin/python tools/index_parallel_benchmark.py --mode docx --documents 24
.venv/bin/python tools/index_parallel_benchmark.py --mode controlled --documents 24
```

Der Benchmark setzt die Workerzahl für den Vergleich ausdrücklich auf 1 bzw. 4.
Produktiv gilt das berechnete Ressourcenbudget. Die Messungen verwenden
ausschließlich erzeugte Dokumente und temporäre Datenbanken; sie sagen keine
konkrete Beschleunigung auf der Zielhardware voraus. Produktive Kundendaten
wurden nicht geöffnet und der laufende Produktivserver wurde nicht verändert.
