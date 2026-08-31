# Changelog

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
