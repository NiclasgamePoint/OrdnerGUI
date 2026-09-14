# Versionierung und Releasevorbereitung

Stand: **14. September 2026**, Ausgangscommit `9bea784`, zusätzlich die lokalen
Releasevorbereitungen. **Noch keine öffentliche Releasefreigabe.**

## Aktueller Versions- und Dokumentationsstand

| Bestandteil | Version / Zustand |
| --- | --- |
| Desktopclient | 0.4.3 |
| Headless-Server | 0.4.3 |
| Contracts | 0.4.2; API v2, Generationsschema v2 |
| Python in CI / neuer lokaler Buildumgebung | 3.11; lokal 3.11.15 |
| Bisherige lokale `.venv` | Python 3.14.2, nicht die Release-Testumgebung |
| PySide6 / PyInstaller | 6.11.1 / 6.22.2, durch Locks festgelegt |
| Remote-Tags | Am 14.09.2026 mit `git ls-remote --tags origin` geprüft: nur `v0.1` |
| GitHub Actions / öffentliche Releases | Über den verbundenen GitHub-Connector nicht prüfbar (HTTP 404) |

Keine künstliche Erhöhung auf 1.0: Zuerst die Abnahme abschließen und dann die
Zielversion festlegen. Empfehlung: 0.4.2 zunächst auf GitHub als Vorabrelease
kennzeichnen. Eigene RC-Versionssuffixe benötigen zusätzlich eine Zuordnung zu
gültigen numerischen macOS-Bundleversionen. `v0.4.1` ist eine historische
Codebezeichnung, kein bestätigter Tag.

Die Projektstruktur bleibt `packages/{contracts,server,client}/src`, ergänzt um
`deploy/server`, `packaging/client`, `tests`, `tools` und `docs`. Keine Rückkehr zu
einem monolithischen Paket. Aktuelle Anleitungen: [Installation](installation.md),
[Betrieb](server/operations.md), [Architektur](architecture.md),
[API](api.md), [Migration](data-and-migrations.md), [Roadmap](roadmap.md).
Veraltete Rootpläne, die Graphify-Weiterleitungsdatei `extras.md`, Prüfberichte und
Auditdaten vom 9. September sowie der unreferenzierte Root-Screenshot wurden
entfernt. Ihre historische Fassung bleibt in Git. Fachliche Handbücher, ADRs,
synthetische Testfixtures und aktuelle Performance-/Icon-Dokumentation bleiben.

## Konkrete Freigabecheckliste

- [x] Paketversionen, Locks, Buildmatrix und Deployment-Tags vergleichen.
- [x] GPL-3.0-or-later hinzufügen; Paketlizenzdateien und Driftprüfung einrichten.
- [x] Hauptabhängigkeiten und Datenquellen nennen; Installer mit verfügbaren
  Lizenztexten und plattformspezifischem Python-Inventar ausstatten.
- [x] Windows-Setup, macOS-PKG und Ubuntu-DEB implementieren; Installation und
  Programmstart in den Artefaktworkflow aufnehmen.
- [x] Lokalen Windows-Prüfweg sowie Linux-/Docker-Prüfung auf diesem PC ermöglichen.
- [x] Linux-DEB lokal bauen und in Ubuntu 24.04 Installation, Start, Lizenzdateien
  und Deinstallation prüfen (Container, keine interaktive Desktopabnahme).
- [ ] Native macOS-Builds für Intel und ARM64 tatsächlich bauen und abnehmen;
  Definitionen allein sind kein Nachweis.
- [ ] Auf frischen Zielsystemen GUI, PDF-/Office-Vorschau, Tray, Pfadmapping,
  Erstinstallation, Upgrade, Offlinebetrieb und Deinstallation prüfen.
- [ ] Vollständige native SBOM und Fremdlizenztexte ergänzen; zugehörige Quellarchive
  für GPL/LGPL-Komponenten einschließlich Qt/PDFium/Python verfügbar machen.
- [ ] Rechte an der ursprünglichen Logovorlage und früheren Fremdbeiträgen klären.
- [ ] Windows-Code-Signing und Apple Developer ID Application/Installer einrichten;
  macOS notarisieren und Ticket anheften. Unsignierte Testinstaller sind vorhanden.
- [ ] Server `linux/amd64` **und** `linux/arm64` abnehmen, Image-Digest und
  Container-SBOM dokumentieren; Backup/Restore und Migration praktisch prüfen.
- [ ] Fachliche Abnahme der Kundenerkennung und Ressourcenverbrauch auf Zielhardware.
- [ ] Zielversion und Releasecommit einfrieren; alle Actions-Ergebnisse genau dieses
  Commits prüfen, Release Notes und Prüfsummen erstellen.
- [ ] Repository und Downloads öffentlich zugänglich machen beziehungsweise deren
  Sichtbarkeit verifizieren; Komponententags, GitHub Release und Serverimage publizieren.

## Installerentscheidung

Windows: Inno-Setup-EXE pro Benutzer mit Startmenü und Uninstaller.
macOS: native PKG-Datei je Architektur, beide Apps gemeinsam in
`/Applications/PapaGUI`. Linux: DEB mit Paketabhängigkeiten für Ubuntu 24.04 x64;
für weitere Distributionen anschließend Flatpak. Ein DEB ist kein universeller
Linuxinstaller. Einzelheiten und Grenzen stehen in [Installation](installation.md).

Der Client enthält Python und Qt. Der Server wird separat über Docker/Compose
installiert; ein Komplettinstaller einschließlich Docker und automatischer
Servereinrichtung ist noch nicht implementiert. Die Trennung ermöglicht mehrere
Arbeitsplätze an einem NAS/Server ohne Docker auf jedem Desktop.

## Pipeline auf dem eigenen PC

Ja: dieselben Test-/Buildbefehle können hier ausgeführt werden. Die neue
`build/release-venv` enthält Python 3.11 und die gepinnten Abhängigkeiten. Start:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_release_checks.ps1
# Zusätzlich Windows-Bundles und Installer bauen:
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_release_checks.ps1 -BuildClient
```

Der lokale Inno-Setup-6.7.3-Compiler liegt unter `build/inno-setup`; unter anderen
Installationen `-Iscc <Pfad>` angeben. Die Umgebung lässt sich mit den Locks aus
der README sowie `packaging/requirements-build-lock.txt` und
`packaging/client/requirements-build-lock.txt` neu anlegen. Das Skript stoppt beim
ersten fehlgeschlagenen Check. Für ACL-Tests eine normale lokale PowerShell nutzen;
eine eingeschränkte Agent-Sandbox kann nach absichtlicher Rechtehärtung den
Zugriff verlieren. Sicherheitsprüfungen deshalb nicht im Produkt abschalten.

Windows testet native Windows-Binaries, Docker Linux-Tests und das Serverimage.
Ein Mac-Build braucht einen Mac; PyInstaller ist kein Cross-Compiler. Lokale Läufe
aktualisieren keinen GitHub-Checkstatus. Für vollständig integrierte Actions auf
dem PC ist ein selbst gehosteter Runner möglich. Empfehlung: ein eigener,
wegwerfbarer Runner für manuell ausgelöste, vertrauenswürdige Commits; öffentliche
PRs weiter auf GitHub-Runnern ausführen, da ein Runner fremden Code ausführt.
Dieser Rechner enthält produktive Daten, daher ist ein dauerhaft direkt darauf
laufender öffentlicher PR-Runner ungeeignet. Registrierung/Runnerdienst sind noch
nicht eingerichtet. Ob lokal schneller, zeigen Messungen; keine pauschale Zusage.

Quellen: [GitHub Self-hosted Runner](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners),
[PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html).

## Lokale Prüfnachweise

- Windows: 1.167 Tests bestanden, fünf plattformbedingte Skips, **94,70 %**
  Branch-Coverage (Mindestgrenze 93 %);
  Log `build/release-quality-windows.log`.
- Windows-Binaries: PyInstaller-Grenzprüfung, `--help`, Offline-Synchronisation bestanden.
- Windows-Setup: Installation in temporärem Ordner, erneute Installation,
  Start und Deinstallation bestanden; `build/release-installer-smoke.log`.
- Alle drei Wheels und sdists gebaut; Artefaktgrenzen, OpenAPI-Snapshot, Ruff
  und `pip check` bestanden; `build/release-wheels.log`.
- Serverimage lokal gebaut; Docker-Smoke mit synthetischen Daten, Persistenz,
  Quellschutz und kontrolliertem Neustart bestanden; `build/release-docker-smoke.log`.
- Linux: vollständiger Python-3.11-Lauf im Debian-12-Container bestanden:
  **1.154 Tests**, 19 betriebssystembedingte Skips, gerundet **95 %** Coverage;
  `build/release-quality-linux.log`. Zusätzlich bestand ein neuer Windows-Test
  für plattformunabhängige Fingerprints separat (sechs Graphify-Tests insgesamt).
- Linux-Binaries und DEB auf Debian 12 gebaut; auf einem frischen Ubuntu-24.04-
  Container Installation, Client/Tray-Start, Lizenzdateien mit Modus 0644 und
  Deinstallation bestanden; `build/release-installer-ubuntu.log`.
- Zusätzliche Paketierungsregression (Lizenzpfad und Dateirechte) auf Windows
  und Linux bestanden. Die volle Suite wurde danach nicht erneut wiederholt.

Die getesteten Installer liegen unter `dist/installers/`:
`papagui-client-0.4.2-windows-x64-setup-unsigned.exe` und
`papagui-client-0.4.2-linux-x64.deb`, jeweils mit `.sha256`-Datei.
Es gibt noch kein lokal gebautes macOS-PKG.

Die Checks prüfen den lokalen Arbeitsbaum, nicht einen veröffentlichten Commit.
Interaktive Bedienung und native macOS-Ausführung sind dadurch nicht belegt.

Client und Server besitzen unabhängige SemVer-Versionen. Das interne
Contracts-Paket folgt der Version des Vertragsstands, ist aber kein drittes
Nutzerprodukt.

Die kanonischen Quellen sind `papagui_client.__version__`,
`papagui_server.__version__` und `papagui_contracts.__version__`; die drei
`pyproject.toml`-Dateien lesen ihre jeweilige Version dynamisch daraus. Native
Client-Bundlemetadaten lesen ebenfalls die Clientquelle. Deployment-Tags und
Buildmatrix werden durch Versionstests gegen diese Quellen geprüft.

## Tags

Gemeinsamer Testcheckpoint: `0.4.3`, annotiert mit `PreRelease Testing`.
Siehe [Actions-Korrekturen](development/ci-043.md).

- Historische Codebezeichnung: `v0.4.1` (Remote-Tag nicht vorhanden)
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
- Veröffentlichung der gebauten Installer
- Code Signing und macOS Notarisierung; automatische Updates sind spätere Produktarbeit

Ob bereits externe Releases, Tags oder Registry-Images existieren, muss am
jeweiligen Dienst geprüft werden und lässt sich aus diesen Dateien nicht ableiten.
