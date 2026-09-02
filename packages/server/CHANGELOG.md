# papagui-server Changelog

## Unreleased

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
- Clienttoken, Argon2id-Adminpasswort und kurzlebige speicherresidente
  Adminsitzungen.
- API v2 und enger, authentifizierter v1-Kompatibilitätsadapter.
- Wiederholbare v0.4.1-Datenmigration für Projekte, Recognition-Fälle und
  Vorschläge sowie Mount-Identitätsschutz gegen leere oder vertauschte Quellen.
- Persistente Retention-Diagnose und argv-sichere Adminpasswort-Erzeugung.
- Multiarch-Dockerdefinition für `linux/amd64` und `linux/arm64`.

Historische gemeinsame Änderungen bis einschließlich 0.4.1 stehen im
[Repository-Changelog](../../CHANGELOG.md).
