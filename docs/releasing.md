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

Bei einem gemeinsamen Entwicklungsstand können Client- und Servertag auf
denselben Commit zeigen. Spätere Versionen dürfen auseinanderlaufen. Vor einem
Tag vorhandene lokale und Remote-Referenzen ausdrücklich prüfen; diese Anleitung
ist kein Nachweis des aktuellen GitHub- oder Veröffentlichungsstands.

## Prüfungen vor einem Tag

1. Arbeitsbaum und Versionsmetadaten prüfen.
2. Lint, vollständige Tests und Coverage-Gate ausführen.
3. Clientartefakte und Serverimage bauen und per Smoke-Test starten.
4. OpenAPI-, Dependency- und Architekturdrift prüfen.
5. Den ausschließlich aus Paketquellen erzeugten Graphify-Graphen und seinen
   Commitbezug gemäß [Runbook](development/graphify.md) kontrollieren.
6. Annotierten Tag lokal erstellen und dessen Commit prüfen.

Python-Distributionen werden aus dem vollständigen
`packaging/requirements-build-lock.txt` mit `python -m build --no-isolation`
gebaut. Der Job `python-distributions` in `quality.yml` wartet auf Server-,
Client-, Wire- und Repository-/Coverage-Jobs. Native Clientartefakte haben einen
separaten manuell auslösbaren Workflow mit eigenen Quelltests; sie werden auf
dem jeweiligen Zielrunner gebaut, auf verbotene Module geprüft und minimal
gestartet. Der Serverimage-Workflow definiert Linux-amd64/arm64-Builds ohne Push
sowie einen separaten Docker-Smoke. Workflowdefinitionen ersetzen keine Prüfung
der tatsächlichen Ergebnisse für den freizugebenden Commit.

## Veröffentlichungsschritte

Die vorhandenen Builddefinitionen erzeugen nicht signierte Artefakte und
enthalten keine Releaseveröffentlichung; `server-image.yml` setzt `push: false`.
Folgende Schritte gehören zu einer gesonderten Veröffentlichung:

- Push der Komponententags
- GitHub Release
- Push in eine Containerregistry
- Veröffentlichung von Installer, DMG oder AppImage
- Code Signing, macOS Notarisierung und automatisches Update

Ob bereits externe Releases, Tags oder Registry-Images existieren, muss am
jeweiligen Dienst geprüft werden und lässt sich aus diesen Dateien nicht ableiten.
