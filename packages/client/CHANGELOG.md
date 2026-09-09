# papagui-client Changelog

## Unreleased

- Sperrlistenänderungen lassen sich vormerken und gemeinsam bestätigen.
  Mehrfachauswahl, Zurücknehmen und Verwerfen halten die Änderungen vor dem
  Speichern überprüfbar. Fehler, Aktualisieren und Tabwechsel erhalten die
  Vormerkungen; eine ausstehende Veröffentlichung kann separat wiederholt werden.
  Der Server meldet sie auch beim Neustart des Clients. Unbestätigte Vormerkungen
  selbst bleiben flüchtiger Zustand des Tray-Prozesses.
- Die Bestätigung sendet eine einzige Batch-Anfrage mit einem HTTP-Zeitlimit
  von mindestens 60 Sekunden für die Veröffentlichung. Statusabfragen bleiben unabhängig
  davon; Server ohne Batch-Unterstützung melden einen Updatehinweis.
- Bei gemischter Mehrfachauswahl werden bereits vorgemerkte Entfernungen
  beibehalten; nur eine vollständig zur Entfernung markierte Auswahl wird
  gemeinsam zurückgenommen.

- Indexserver und Kundenerkennungsneuaufbau zeigen aktive Dokument-Worker,
  ermittelte Obergrenze, Warteschlange und verarbeitete, wiederverwendete,
  extrahierte sowie fehlerhafte Dokumente live an. Ältere Server bleiben
  kompatibel; die Übersicht weist auf fehlende Workerstatistiken hin, der
  Neuaufbau zeigt weiterhin seinen allgemeinen Jobstatus.
- Der Ressourcenprofil-Tooltip erläutert die Berechnung aus CPU, RAM und
  Containergrenzen; die Dokumentkarte zeigt die tatsächlich verwendete Grenze.

- Der Tab „Kundenerkennung“ auf der Indexserver-Seite enthält die serverweite
  Sperrliste und den vollständigen Neuaufbau mit Status und Abbruch. Eingaben
  bleiben beim Tabwechsel erhalten; ausgeblendete Tabs pausieren Statusabfragen.
  Der Tab bleibt bei Verbindungsfehlern erreichbar und scrollt in kleinen Fenstern.

- Dropdown „Art“ im Kundeneditor öffnet sich auch beim Klick auf die Textfläche;
  vorhandene Kundenarten außerhalb der Standardauswahl bleiben erhalten.
- Kundendetailseiten öffnen sofort und laden Projektmetadaten, Journalzustand
  und serverseitige Vorschläge asynchron; doppelte Requests sowie verspätete
  Antworten eines zuvor gewählten Kunden werden verworfen.
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
- Dokumentation an die kennwortfreie Client-Token-Steuerung, aktuelle
  Kundenerkennungsbedienung und Plattformgrenzen angepasst; native
  Plattformabnahme wird nicht aus synthetischen Tests abgeleitet.

### Kundendatenprüfung

- Gruppierte Vorschläge mit Quellen, Konflikten, Pagination und aktueller Revision nach jeder Entscheidung.
- Kundenbezogene Aktionen **Neu bewerten** und **Neu auslesen**, nachvollziehbare Abdeckung, lesbarer Offline-Stand und stabile Kontakt-IDs.
- Kundenprüfung visuell überarbeitet: kompakte Karten, aufklappbare Belege, erreichbare Entscheidungen, umbrechende Texte und gekürzte Quellnamen ohne horizontales Scrollen; Hell-/Dunkelmodus und kleine Dialoggrößen mit synthetischen Daten geprüft.

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
