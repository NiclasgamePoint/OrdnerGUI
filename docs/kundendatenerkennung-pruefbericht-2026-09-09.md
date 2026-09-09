# Prüfbericht zur Kundendatenerkennung

Datum: 9. September 2026. Geprüft wurde der lokale Entwicklungsstand nach Umsetzung der neuen Pipeline. Alle fachlichen Eingaben waren ausdrücklich synthetisch. Bestehende Kundendatenbanken, Dokumentordner und der frühere Bestandsaudit wurden für die Implementierung nicht in den Assistenzkontext eingelesen. Es wurde kein Produktivbestand migriert oder veröffentlicht.

| Prüfung | Ergebnis |
|---|---|
| Vollständiger isolierter Testlauf | 651 Tests in 68 Modulen bestanden, einschließlich echtem lokalem HTTP-Verbindungstest. |
| Letzte anschließende GUI-Korrektur | 15 betroffene Reviewtests bestanden; unvollständige Anschriften öffnen die manuelle Bearbeitung. |
| Coverage einschließlich dieser Nachprüfung | 95,15 % Statements/Zweige kombiniert; Projektgrenze 93 % erfüllt. |
| Ruff und Diff-Prüfung | Bestanden. |
| Contracts-, Server- und Client-Wheels | Erfolgreich gebaut. |
| Docker-Server unter Python 3.11 | Bau, Abhängigkeitsprüfung und vollständiger vorhandener Smoke-Test bestanden, einschließlich Generationen und kontrolliertem Neustart. |
| Echte Parser/OCR im fertigen Container ohne Netzwerk | DOCX, XLSX, PNG-OCR, 90° gedrehte PNG-OCR und gemischtes PDF bestanden. Dokumente werden vom Test erzeugt und gelöscht. |
| Weitere Formatregressionen | Echte XLS-Dateien, Kopf-/Fußzeilen, führende Nullen, zwei PDF-Spalten, Seiten-/Zeichen-/Speicherbudgets, fehlende OCR-Sprache und Cacheinvalidierung geprüft. |
| Migration | 17 synthetische Vorschauprüfungen: unveränderte Originaldatei/WAL, Werte-/Entscheidungserhalt, erkannter injizierter Verlust, geschützte Berichtsausgabe. |
| Schnittstellen | OpenAPI-Snapshot aktualisiert; alte Migrationsfixtures bleiben prüfbar; ältere Clientantworten und Offline-Snapshots getestet. |

## Synthetischer fachlicher Vergleich

Der reproduzierbare Benchmark enthält 54 selbst verfasste Fälle in 17 erfundenen Kundengruppen. Kundengruppen überschneiden sich nicht zwischen den gekennzeichneten Testteilen. Dies ist **kein unabhängiger, menschlich annotierter Produktiv-Abnahmebestand**.

| Regelstand | Richtige gut belegte Vorschläge | Falsche | Fehlende erwartete |
|---|---:|---:|---:|
| Frühere Regexregel | 19 | 42 | 8 |
| Neue fachliche Pipeline | 27 | 0 | 0 |

Zusätzlich werden fünf Belege als unklare Prüffälle ausgewiesen. Der Vergleich bewertet die synthetischen fachlichen Eingaben und die Qualitätsstufe `strong`; er misst weder echte OCR-Wiederfindung noch die Präzision sämtlicher angezeigter Prüffälle. Daraus wird keine Prozentgarantie für reale Kunden abgeleitet. Die allgemeinen Zielwerte und eine messbare Reduktion der bisherigen produktiven Vorschlagsmenge benötigen weiterhin die lokale fachliche Abnahme.

## Quellcodegraph

Der Graph wurde einschließlich der Nachbesserungen ausschließlich aus den 162 Python-Laufzeitdateien in `packages/{contracts,server,client}/src` erneuert: 2.491 Knoten und 6.506 gerichtete Beziehungen. Kundendokumente, Auditberichte und Graph-Memories waren keine Extraktionsquellen; es gab keinen semantischen Modellaufruf. Der bisherige generierte Graph wurde lokal als Sicherung erhalten.

Die AST-Ausgabe enthält 488 externe Endpunktreferenzen, die der Builder als Knoten materialisiert, und 380 wiederholte Beziehungen gleicher Endpunkte, die der gerichtete Symbolgraph zusammenfasst. Er ist keine Aufzählung jeder einzelnen Aufrufstelle. Im exportierten Graph gibt es keine fehlenden Endpunkte, losen Kanten oder Selbstschleifen. Die Grenzen stehen auch im lokalen Graphbericht.

## Nachbesserung: Datumsfilter, Kontaktnamen und Serververwaltung

Der vollständige isolierte Testlauf bestand mit 813 Tests in 73 Modulen und 95 % Coverage. Anschließend bestand zusätzlich der neue Regressionstest für die Veröffentlichung nach dem Entfernen einer Sperre. Die fünf zuletzt betroffenen Module wurden nochmals isoliert geprüft: 88 Tests bestanden. Ruff, Diff-Prüfung und der aktualisierte OpenAPI-Vertrag bestanden ebenfalls.

Die Prüfungen verwenden ausschließlich erzeugte Dokumente, temporäre Datenbanken und erfundene Kontakte. Sie decken zusätzliche Datumsschreibweisen und Datumskontexte, plausible internationale Personennamen, die Bereinigung bereits migrierter offener Altvorschläge, normalisierte globale Sperren, Gruppen- und Domainregeln sowie Offline-Snapshots ab. Die Oberfläche wurde mit dem produktiven Stylesheet bei 760 × 560 und 560 × 420 geprüft; sie hat keinen horizontalen Überlauf und behält eine erreichbare Fußzeile.

Zwei aufeinanderfolgende vollständige Neuaufbauten lasen unveränderte synthetische Dokumente jeweils erneut ein und überschritten das normale Dokumentbudget. Manuelle Daten, Kontakt-IDs, Herkunftseinträge und frühere Entscheidungen blieben unverändert. Abbruch, Wiederaufnahme nach einem Serverneustart sowie das Herunterfahren während des Wartens auf den gemeinsamen Schreibzugriff sind durch Regressionstests abgedeckt. Gesperrte Treffer beenden die Suche nach fehlenden Angaben nicht vorzeitig.

Diese Nachbesserung wurde im Arbeitsstand implementiert und getestet; ein produktiver Neuaufbau mit Kundendokumenten wurde während der Entwicklung nicht ausgelöst.

## Einführung

Die technischen Funktionen und ihre Migration sind implementiert. Die produktive Umstellung erfolgt beim Start der neuen Serverversion mit Sicherung; vorhandene Quellen werden anschließend über reguläre oder gezielte Erkennungsläufe neu bewertet. Die [Betriebsdokumentation](kundendatenerkennung.md) beschreibt Vorschau, Einstellungen, Einführungsablauf und Rückfall. Eine optionale Modellstufe wurde mangels unabhängig belegten Zusatznutzens nicht aktiviert.
