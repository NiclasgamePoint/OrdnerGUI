# Umsetzungsplanung

Stand: 9. September 2026, lokaler Entwicklungsstand 0.4.2. Die Haken bezeichnen
implementierte Funktionen; sie ersetzen weder die native Plattformabnahme noch
die fachliche Abnahme eines Produktivbestands. Die aktuellen Bedien- und
Betriebsanleitungen sind in der [README](README.md#dokumentation) verlinkt.

## Trennung 0.4.2

- [x] Ausgangsstand als `v0.4.1` markieren
- [x] Monorepo-Struktur für Contracts, Server und Client festlegen
- [x] Gemeinsame API- und Generationsverträge isolieren
- [x] Headless-Server ohne Qt-Abhängigkeit paketieren
- [x] API v2 mit Client-Bearer-Token absichern; auch Wartungsaktionen verwenden
  diesen Token, ohne gesondertes Adminpasswort oder Adminsitzung
- [x] Index- und Kundengenerationen trennen
- [x] Clientcache auf atomisches `active-generation.json` umstellen
- [x] Offline-Outbox von unveränderlichen Snapshots trennen
- [x] Logische `source_id`-Pfade und lokales Pfadmapping ergänzen
- [x] Client und Server getrennt buildbar machen
- [x] Import- und Artefaktgrenzen in CI prüfen
- [x] Docker-/Synology-Runbook und Migrationsdokumentation aktualisieren
- [x] Graphify-Aktualität reproduzierbar prüfen
- [x] Getrennte Build- und Prüfabläufe für Client und Server vorbereiten

Komponententags werden gemäß [Release-Ablauf](docs/releasing.md) erst nach der
jeweiligen Abnahme gesetzt und geprüft. Die enthaltenen Workflows veröffentlichen
keine Releases und pushen keine Serverimages in eine Registry.

## Kundenerkennung und Dokumentverarbeitung

- [x] Strukturierte lokale Auslesung, Feldvalidierung und Rollenprüfung ergänzen
- [x] Datumsangaben als Telefonkandidaten abweisen und benannte Kontaktblöcke prüfen
- [x] Vorschläge nach Information gruppieren und mehrere Belege erhalten
- [x] Manuelle Stammdaten, Kontakt-IDs und frühere Entscheidungen schützen
- [x] Abdeckungsstatus, Ergänzungen, Konflikte und unklare Belege im Client anzeigen
- [x] Texte neu bewerten und Dokumente für einzelne Kunden gezielt erneut lesen
- [x] Serverweite Ausschlüsse und vollständigen Neuaufbau im Indexserver-Tab verwalten
- [x] Mehrere Sperrlistenänderungen vormerken und gemeinsam bestätigen oder verwerfen
- [x] Fehlgeschlagene Kundenveröffentlichung nach gespeichertem Batch wiederholen
- [x] Gemeinsame parallele Dokumentauslesung und Extraktionscache verwenden
- [x] Metadatenabgleich, tägliche Hashprüfung und erzwungene Neuauslesung trennen
- [x] Workerbudget, Warteschlange und Dokumentzähler im Serverstatus anzeigen
- [x] Migration, Fehlerfälle und fachliche Regeln mit synthetischen Daten prüfen

Details: [Kundenerkennung](docs/kundendatenerkennung.md),
[API](docs/api.md), [Prüfberichte](README.md#dokumentation).

## Noch ausstehende Abnahme

- [ ] Native Windows-Ausführung der Token-/ACL-Regressionstests sowie GUI- und
  Docker-Erststart auf dem Zielrechner bestätigen
- [ ] Native Clientartefakte auf den jeweiligen Zielplattformen abnehmen
- [ ] Kundenerkennung lokal an einem unabhängig fachlich bewerteten Referenzbestand
  beurteilen; synthetische Ergebnisse sind keine zugesicherte Produktivqualität
- [ ] Laufzeit und Ressourcenverbrauch auf der tatsächlichen Zielhardware messen
- [ ] Backup und Restore im vorgesehenen Zielbetrieb praktisch abnehmen

Die historischen Messungen stehen in den datierten Prüfberichten. Die aktuelle
Testsuite und der jeweilige CI-Lauf müssen für den auszuliefernden Commit geprüft
werden. Konkrete Kundendaten bleiben auch bei der Abnahme außerhalb des
Assistenzkontexts.

## Nächste Produktphasen

- [ ] Feldweise grafische Auflösung von Kundenkonflikten weiter verfeinern
- [ ] Clientinstaller auf echten Windows-, macOS- und Linux-Systemen signieren
- [ ] Multiarch-Serverimage in einer Registry veröffentlichen
- [ ] Konkretes Synology-Modell im Dauerbetrieb und bei Volume-Ausfall testen
- [ ] Rollenmodell über den gemeinsamen Client-Bearer-Token hinaus erweitern
- [ ] Automatische Clientupdates nach Signaturkonzept ergänzen

## Spätere Funktionen

- [ ] IMAP-Postfach anbinden und Zugangsdaten ausschließlich im OS-Keyring halten
- [ ] E-Mails und Anhänge serverseitig indizieren und Kunden zuordnen
- [ ] Drag-and-drop für Dateiimport und externe Anwendungen ergänzen
- [ ] Fokusreihenfolge und Screenreader-Verhalten vollständig auditieren

## Qualitätsregeln

- Coverage-Gate mindestens 93 Prozent
- keine verbotenen Paketimporte
- keine lokale Indexerzeugung im Produktionsclient
- keine Qt-Abhängigkeit im Server
- Schema- und API-Änderungen nur mit Golden-Fixtures und Migration
- Dokumentation, OpenAPI-Snapshot und Graphify-Fingerprint müssen aktuell sein
