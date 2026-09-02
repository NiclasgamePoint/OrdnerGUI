# Changelog

## Unreleased

## 0.4.2 - Client/Server Split

- Repository in die unabhängigen Pakete `papagui-client`, `papagui-server` und
  das interne `papagui-contracts` gegliedert; der frühere Monolith gehört nicht
  mehr zu einem Produktartefakt.
- Headless FastAPI-Server als alleinigen Writer für portable Indexgenerationen,
  Kundenerkennung und die maßgebliche `customers.db` eingeführt.
- Komponentenweise Generationsmanifeste, SHA-256-Prüfung, atomare Aktivierung
  und jeweils drei gültige Vorgängergenerationen auf Server und Client ergänzt.
- Read-only-Desktopclient mit Start-/Intervallsynchronisation, plattformneutralem
  Quellpfadmapping und vollständigem Offlinebetrieb ergänzt.
- Kundenänderungen über eine dauerhafte Outbox, Idempotency-Keys und
  Revisionskonflikte mit expliziter 409-Auflösung abgesichert.
- Eigenständiges Admin-Tray mit Live-Serverstatus, Indexsteuerung,
  serverseitigen Einstellungen und ausschließlich speicherresidenten
  Adminsitzungen ergänzt.
- Leere oder vertauschte Quellmounts durch eine persistente Quellenidentität
  abgesichert und Secretdateien/Clientkonfiguration plattformgerecht gehärtet.
- Getrennte Docker-, native Client- und CI-Builddefinitionen sowie Architektur-,
  OpenAPI-, Artefakt- und Graphify-Driftprüfungen ergänzt.

## 0.4.1 - Pre-Refactor Checkpoint

- Systemtray-Indexfenster zur Docker-Serverkonsole mit Live-Status, Jobfortschritt,
  Generationen, Wartungsaktionen und kontrolliertem Containerneustart umgebaut.
- Servereinstellungen für OCR, Ressourcen, Formate, Priorisierung und Laufintervall
  aus der Haupt-GUI in die authentifizierte Indexserver-Konsole verschoben.
- Indexserver-Konsole als eigenständige, vom MainWindow unabhängige Tray-Anwendung
  mit Einmalinstanz-Aktivierung, automatischem Start durch `start.sh` und
  separatem `index-tray.sh`-Launcher ergänzt.
- Tab-Leiste der Indexserver-Konsole eingerückt, damit sie nicht mehr mit der
  abgerundeten linken Fensterkante überlappt.
- Qt-Absturz beim Schließen der eigenständigen Indexserver-Konsole behoben:
  Das Fenster wird ins Tray ausgeblendet und laufende Serverabfragen werden beim
  echten Beenden kontrolliert abgeschlossen.
- Server-Badge mit grünem Onlinepunkt, rotem Offlinepunkt, orangem Problemstatus
  und animiertem grünem Halbkreis während eines Indexlaufs ergänzt.
- Automatisches Laufintervall in Zahlenfeld und Minuten-/Stundenauswahl getrennt,
  auf 15 Minuten bis 48 Stunden begrenzt und serverseitig dauerhaft gespeichert;
  Live-Aktualisierungen überschreiben ungespeicherte Eingaben nicht mehr.

- Docker-Indexer zur alleinigen Indexquelle erweitert.
- Unveränderliche, SHA-256-geprüfte Generationen mit drei Serverbackups ergänzt.
- Automatischen Clientdownload, atomare Aktivierung, drei lokale Backups und
  Offline-Fallback ergänzt.
- Authentifizierte Kunden-API mit Revisionen, HTTP-409-Konflikten und dauerhafter
  Offline-Queue ergänzt.
- Coverage-Gate für die nun zusätzlich real getesteten HTTP-/Prozessadapter auf
  weiterhin projektweite 93 Prozent kalibriert; Adapter bleiben in der Messung.

## 0.4.0

- Headless-Docker-Dienst für Katalog, Kundenerkennung und Inhaltsindexierung.
- Compose-Beispiel mit read-only Quelldaten und persistenten Daten-/Config-Mounts.
- Isolierung der MainWindow-Workflow-Tests; der blockierende Indexworkflow-Test
  startet keine echten Controller oder Worker mehr.
- Migration des PDF-Fallbacks von deprecated `PyPDF2` auf `pypdf`.
- Aktuelle `QMouseEvent`-Signaturen in den Widgettests.
- Portable QSettings-Speicherung gilt nun für Ini- und native Qt-Formate, damit
  der Docker-Config-Mount zuverlässig verwendet wird.
- Bereinigte Coverage-Warnungen für absichtlich nicht importierte Module in
  isolierten Testprozessen.

## 0.3.0

- Aufteilung des Indexes in schnellen Katalog und fortsetzbare Inhalts-Shards.
- Erweiterte Kundenverwaltung, Kundenerkennung, Viewer und Indexdiagnose.
