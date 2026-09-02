# Umsetzungsplanung

## Trennung 0.4.2

- [x] Ausgangsstand als `v0.4.1` markieren
- [x] Monorepo-Struktur für Contracts, Server und Client festlegen
- [x] Gemeinsame API- und Generationsverträge isolieren
- [x] Headless-Server ohne Qt-Abhängigkeit paketieren
- [x] API v2 mit Client- und Adminauthentifizierung ergänzen
- [x] Index- und Kundengenerationen trennen
- [x] Clientcache auf atomisches `active-generation.json` umstellen
- [x] Offline-Outbox von unveränderlichen Snapshots trennen
- [x] Logische `source_id`-Pfade und lokales Pfadmapping ergänzen
- [x] Client und Server getrennt buildbar machen
- [x] Import- und Artefaktgrenzen in CI prüfen
- [x] Docker-/Synology-Runbook und Migrationsdokumentation aktualisieren
- [x] Graphify-Aktualität reproduzierbar prüfen
- [x] Abschlussstand für `client-v0.4.2` und `server-v0.4.2` vollständig vorbereiten

Beide annotierten Tags werden unmittelbar nach der vollständigen Abnahme und
dem finalen Graphify-Lauf auf denselben Abschluss-Commit gesetzt und danach
gegeneinander geprüft. Die Haken bedeuten keine Veröffentlichung; Releases und
Registry-Push bleiben gesperrt.

## Nächste Produktphasen

- [ ] Feldweise grafische Auflösung von Kundenkonflikten weiter verfeinern
- [ ] Clientinstaller auf echten Windows-, macOS- und Linux-Systemen signieren
- [ ] Multiarch-Serverimage in einer Registry veröffentlichen
- [ ] Konkretes Synology-Modell im Dauerbetrieb und bei Volume-Ausfall testen
- [ ] Rollenmodell über gemeinsames Client/Admin hinaus erweitern
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
