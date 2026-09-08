# papagui-client Changelog

## Unreleased

- Einen GUI-Hänger mit anschließendem `SIGABRT` bei Volltextsuchen behoben:
  Die FTS-Ergebnismenge wird jetzt einmal je Suche statt einmal je Katalogzeile
  berechnet.
- Hauptsuche auf Kunden und Projektordner fokussiert, redundante Debounce-Läufe
  verhindert und den Dateitypfilter ausgeblendet; Unterordner bleiben optional.
- Projektunterordner der Dienstleistungsseite laden beim Aufklappen ihre
  Ordner- und Dateiinhalte aus der aktiven lokalen Generation nach.

- Passwortdialoge und Adminsitzungszustand aus Tray und Kundenerkennungsprüfung
  entfernt; Serveraktionen sind nach gültiger Client-Token-Verbindung direkt
  bedienbar.

- Vollständiges v0.4.1-Kartenlayout für Hauptfenster, Suche, Ordner, Kunden,
  Einstellungen, Viewer und eigenständiges Server-Tray wiederhergestellt, ohne
  die Client-/Server-Paketgrenzen oder den read-only Indexzugriff aufzuweichen.
- Neue 0.4.2-Synchronisations-, Offline-, Konflikt-, Pfad- und Adminfunktionen in
  die wiederhergestellten Bedienelemente integriert und verbleibende UX-Punkte
  dokumentiert.
- Alte Statistikansicht mit lokalen Kunden- und Suchkennzahlen sowie die
  Weiterleitung **Indexserver öffnen** zum eigenständigen Tray wiederhergestellt.

## 0.4.2

- Erstes eigenständig versioniertes Clientpaket mit Hauptanwendung und
  unabhängigem Tray-Prozess.
- Unveränderliche lokale Index- und Kundensnapshots, atomarer JSON-Zeiger und
  drei Vorgängergenerationen.
- Automatische Synchronisation beim Start und im konfigurierten Intervall mit
  Größen-, Schema- und SHA-256-Prüfung.
- Read-only-Katalogsuche sowie Windows-, macOS- und Linux-Pfadauflösung über
  `source_id`.
- Offline-First-Kundenansicht mit Overlay, dauerhafter Outbox, Replay und
  bewusster Auflösung von Revisionskonflikten.
- Eigene Journal-Outbox sowie Review-Oberflächen für serverseitige
  Kundenerkennung und dokumentbasierte Vorschläge.
- Eigenständige Tray-Composition-Root und zwei benachbarte macOS-App-Bundles.
- Geschützte Clientkonfiguration mit POSIX-Modus `0600`, Windows-ACL-Härtung
  und diagnostizierbarer Generationsbereinigung.
- Dokumentvorschauen für PDF, Text, Office-, Tabellen- und Bilddateien.

Historische gemeinsame Änderungen bis einschließlich 0.4.1 stehen im
[Repository-Changelog](../../CHANGELOG.md).
