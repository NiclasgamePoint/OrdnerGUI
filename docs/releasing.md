# Versionierung und Releasevorbereitung

Client und Server besitzen unabhängige SemVer-Versionen. Das interne
Contracts-Paket folgt der Version des Vertragsstands, ist aber kein drittes
Nutzerprodukt.

Die kanonischen Quellen sind `papagui_client.__version__`,
`papagui_server.__version__` und `papagui_contracts.__version__`; die drei
`pyproject.toml`-Dateien lesen ihre jeweilige Version dynamisch daraus. Native
Client-Bundlemetadaten lesen ebenfalls die Clientquelle. Deployment-Tags und
Buildmatrix werden durch Versionstests gegen diese Quellen geprüft.

## Tags

- Legacy-Gesamtstand: `v0.4.1`
- Client: `client-v<version>`
- Server: `server-v<version>`

Für den ersten getrennten Entwicklungsstand zeigen `client-v0.4.2` und
`server-v0.4.2` auf denselben Commit. Spätere Versionen dürfen auseinanderlaufen.

## Prüfungen vor einem Tag

1. Arbeitsbaum und Versionsmetadaten prüfen.
2. Lint, vollständige Tests und Coverage-Gate ausführen.
3. Clientartefakte und Serverimage bauen und per Smoke-Test starten.
4. OpenAPI-, Dependency- und Architekturdrift prüfen.
5. Graphify vollständig aktualisieren und Commitbezug kontrollieren.
6. Annotierten Tag lokal erstellen und dessen Commit prüfen.

Python-Distributionen werden aus dem vollständigen
`packaging/requirements-build-lock.txt` mit `python -m build --no-isolation`
gebaut. Der Artefaktjob wartet auf Server-, Client-, Wire- und vollständiges
Repository-/Coverage-Gate. Native Clientartefakte werden auf dem jeweiligen
Zielrunner gebaut, auf verbotene Module geprüft und minimal gestartet.

## Noch nicht freigegeben

Der Stand 0.4.2 erzeugt nur nicht signierte lokale oder CI-Artefakte. Folgende
Aktionen erfolgen erst nach einer eigenen Freigabeentscheidung:

- Push der Komponententags
- GitHub Release
- Push in eine Containerregistry
- Veröffentlichung von Installer, DMG oder AppImage
- Code Signing, macOS Notarisierung und automatisches Update
