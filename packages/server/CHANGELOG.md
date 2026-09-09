# papagui-server Changelog

## Unreleased

- Atomare Stapelschnittstelle für Sperrlistenänderungen: einmaliger Abgleich
  bestehender Vorschläge und eine Veröffentlichung pro geändertem Stapel.
  Wiederholte unveränderte Anfragen vermeiden zusätzliche Veröffentlichungen;
  fehlgeschlagene Veröffentlichungen bleiben dauerhaft zur Wiederholung vorgemerkt.
  Der Listenendpunkt meldet den offenen Veröffentlichungszustand auch nach
  einem Neustart. Monotone IDs schützen neue Sperren vor veralteten Löschanfragen.
  Sperrlistenänderungen starten keine Dokumentauslesung und keinen Neuaufbau.

- Gemeinsame parallele Dokumentauslesung für Indexierung und Kundenerkennung
  mit begrenzter Warteschlange und 1–20 Workern. Ressourcenprofile berücksichtigen
  CPU-Affinität, verfügbaren RAM und Containergrenzen; die fachliche Zuordnung
  erfolgt weiterhin nacheinander.
- Unveränderte Dokumente werden bei regulären Folgeläufen ohne erneutes Lesen
  übersprungen. Täglicher Abgleich und ausdrücklicher Indexneuaufbau prüfen die
  Inhaltshashes; passende Extraktionsergebnisse bleiben wiederverwendbar.
  Der letzte erfolgreiche vollständige Prüftermin bleibt über Neustarts erhalten.
- Der Scheduler beachtet den gespeicherten täglichen Prüftermin auch bei einem
  längeren Indexintervall. `--no-run-on-start` unterdrückt einen sofortigen Lauf
  auch bei fehlendem oder überfälligem Prüftermin; Fehler-/Abbruchwiederholungen
  halten ein begrenztes Warteintervall ein.
- Additive Workerstatistik im Serverstatus und während der Auslesung laufender
  Kunden-/Neuaufbauaufträge: aktive Worker, Limit, Warteschlange und Dokumentzähler.

- Die serverweite Sperrliste lässt sich während laufender Index- und
  Neuaufbauaufträge bearbeiten. Neue Sperren gelten auch für anschließend
  gespeicherte Vorschläge desselben Laufs.

- Datumsangaben mit PDF-/OCR-Abständen werden als Telefonnummern ausgeschlossen;
  Kontaktvorschläge benötigen plausible Namen. Bereits migrierte offene
  Altvorschläge werden ebenfalls geprüft, historische Entscheidungen bleiben erhalten.
- Serverweite Sperrliste für Kontaktwerte und vollständiger, abbrechbarer
  Neuaufbau einschließlich erneuter Dokumentextraktion über authentifizierte API.

- Separate Adminpasswörter und flüchtige Adminsitzungen entfernt. Sämtliche
  Servereinstellungen und Wartungsaktionen verwenden nur noch den Client-Token.

### Kundendatenerkennung

- Strukturierte Office/PDF/OCR-Auslesung mit privatem Cache, getrennten Ressourcenbudgets und nachvollziehbarer Teilabdeckung.
- Normalisierte Kandidaten, Parteien, Adress-/Kontaktgruppen, Entscheidungen und Herkunft; Kunden-Schema 3, additive Sperrliste in Schema 4.
- Koordinierte dauerhafte Kundenjobs und additive Review-/Status-API.

## 0.4.2

- Erstes eigenständig versioniertes, Qt-freies Serverpaket mit FastAPI/Uvicorn.
- Alleinige Index- und Kundenschreibverantwortung mit Scheduler, Abbruch,
  Neustart, Wiederaufnahme und atomarer Veröffentlichung.
- Portable Katalogpfade aus `source_id` und relativem Pfad sowie begrenzte
  Dokumentextraktion einschließlich optionaler OCR.
- Autoritative `customers.db` mit Unit of Work, Migration, Revisionen,
  Idempotenz und sicheren Parallelkonflikten.
- Getrennte Index- und Kundengenerationen mit aktivem Stand plus drei
  Vorgängern.
- Clienttoken und ursprünglich getrennte Adminauthentifizierung.
- API v2 und enger, authentifizierter v1-Kompatibilitätsadapter.
- Wiederholbare v0.4.1-Datenmigration für Projekte, Recognition-Fälle und
  Vorschläge sowie Mount-Identitätsschutz gegen leere oder vertauschte Quellen.
- Persistente Retention-Diagnose.
- Multiarch-Dockerdefinition für `linux/amd64` und `linux/arm64`.

Historische gemeinsame Änderungen bis einschließlich 0.4.1 stehen im
[Repository-Changelog](../../CHANGELOG.md).
