# Versionierung und Releasevorbereitung

Stand: **14. September 2026**. Der gemeinsame Testcheckpoint **0.4.3** trägt den
Tagtext **PreRelease Testing**. Der folgende Entwicklungsstand **0.5.0** ergänzt
[automatische Updates](automatic-updates.md). Das ist noch keine öffentliche
Produktfreigabe. GitHub-Zugriff, Actions und Joblogs sind verfügbar.

Die konkrete [Beta-Arbeitsliste und Signierungsanleitung](beta-readiness.md)
beschreibt den geprüften Stand, Ordner- und Netzwerkgrenzen sowie die noch
vorbereitete Veröffentlichung von GitHub-Pre-releases für Windows und Linux.

## Versionsstand

| Bestandteil | Stand |
| --- | --- |
| Client, Server, Contracts | jeweils 0.5.0; Checkpoint 0.4.3 behält seinen Quellstand; Git-IDs durch Historienbereinigung geändert |
| API / Generationsschema | v2; bestehende Kompatibilitätswege bleiben erhalten |
| Release-Python | 3.11; lokale Testumgebung `build/release-venv` |
| PySide6 / PyInstaller | 6.11.1 / 6.22.2 laut Lockdateien |
| Lizenz | GPL-3.0-or-later |

Kanonisch sind die Versionen in den drei Paketquellen. Die `pyproject.toml`-Dateien
lesen diese dynamisch; `python tools/release_metadata.py` prüft Paketabhängigkeiten,
Buildmatrix, Deployment-Tags und Lizenzkopien. Die bisherige `.venv` mit Python
3.14 ist nicht die Release-Testumgebung.

Die Struktur ist `packages/{contracts,server,client}/src`, ergänzt um
`deploy/server`, `packaging/client`, `tests`, `tools` und `docs`.
Aktuelle Anleitungen: [Installation](installation.md), [Betrieb](server/operations.md),
[Architektur](architecture.md), [API](api.md), [Migration](data-and-migrations.md).
Entfernte alte Pläne und Auditberichte bleiben in der Git-Historie erreichbar.

## Nachweise und offene Abnahme

Die vollständigen lokalen Windows- und Linux-Testläufe für die Vorbereitung von
0.4.3 bestehen mit gerundet 95 % Coverage. Python-Wheels und Quelldistributionen
wurden gebaut und geprüft. Native Linux- und macOS-Installer für Intel und ARM64
wurden inzwischen auf GitHub erfolgreich installiert und gestartet. Der Windows-
Workflow prüft zusätzlich Upgrade und Deinstallation. Fehlerursachen und
Korrekturen stehen im [CI-Bericht](development/ci-043.md).

Für den Tag zählt immer das Ergebnis des **exakten Tagcommits**:

- Quality einschließlich Coverage, Paketgrenzen, OpenAPI und Wire-Interop erfolgreich.
- Alle vier nativen Client-Builds einschließlich Installerprüfungen erfolgreich.
- Server-Build für Linux amd64/arm64 und Docker-Smoke erfolgreich.
- Graphify-Fingerprint und lokaler Graph auf dem Commit aktuell.

Vor einer öffentlichen Produktfreigabe bleiben erforderlich:

- Interaktive Abnahme auf frischen Zielsystemen: GUI, Vorschau, Tray, Pfadmapping,
  Offlinebetrieb und kundenspezifische Abläufe. Automatisierte Starttests ersetzen das nicht.
- Vollständige native Fremdsoftware-/Lizenzinventare und passende Quellarchive für
  die ausgelieferten GPL-/LGPL-Komponenten, einschließlich Qt/PDFium/Python.
- Herkunft des eigenen Codes (Inhaber/Codex) und öffentliche Weitergabe der
  Logovorlage sind bestätigt. Fremdsoftwarehinweise bleiben separat erhalten.
- Windows-Code-Signing sowie Apple Developer ID, Notarisierung und Stapling;
  vorhandene Installer sind ausdrücklich unsignierte Testartefakte.
- Zielhardware, Ressourcenverbrauch und echte Backup-/Wiederherstellungsabläufe abnehmen.
- Repository, Downloads und Containerimage tatsächlich öffentlich bereitstellen;
  Release Notes, Prüfsummen und Image-Digest zuordnen. Sichtbarkeit ändert sich nicht automatisch.

## Installer und lokale Pipeline

Windows erhält eine Inno-Setup-EXE pro Benutzer mit Uninstaller. macOS erhält je
Architektur ein PKG für Client und Tray. Linux erhält ein DEB für Ubuntu 24.04 x64;
weitere Distributionen benötigen eigene Abnahme und gegebenenfalls Flatpak.
Der Client enthält Python und Qt. Der getrennte Server wird über Docker Compose
installiert; ein Komplettinstaller einschließlich Docker ist noch nicht vorhanden.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_release_checks.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_release_checks.ps1 -BuildClient
```

Diese Befehle führen die Windows-Prüfungen auf dem eigenen PC aus. Linux lässt
sich zusätzlich mit Docker prüfen. Native macOS-Builds benötigen einen Mac;
ein einzelner Windows-Runner ersetzt die Betriebssystemmatrix nicht.
GitHub Actions liefert die automatischen Prüfungen und Artefakte für jeden Commit.
Ein eigener Runner kann später registriert werden; derzeit wird keiner installiert.

## Tags und Veröffentlichung

Der gemeinsame Checkpoint heißt exakt `0.4.3`, Annotation `PreRelease Testing`.
Vorhandene lokale und Remote-Tags vor dem Erstellen vergleichen; veröffentlichte
Tags nicht nachträglich verschieben. Das Erstellen eines Tags veröffentlicht noch
kein GitHub Release und installiert kein Update.

Push- und PR-Workflows bauen Testartefakte ohne Registry-Push. Der neue Workflow
`release.yml` reagiert auf stabile veröffentlichte Releases, führt die Prüfungen
aus und lädt erst danach Images, Installer und zuletzt das Updatemanifest hoch.
Für Pre-releases prüft derselbe Workflow Windows/Linux und lädt deren Installer,
das Serverimage sowie `papagui-beta.json` mit dem Digest hoch. Dabei werden
macOS-Pakete und das Aktivierungsmanifest ausgelassen. Dieser neue Pfad ist
lokal getestet; ein tatsächlicher Beta-Release-Lauf steht noch aus.
Die Kompatibilitätsprüfung verwendet den Tag 0.4.3 (Quellstand unverändert,
Commit seit der Historienbereinigung `dbf6c9a`). Vor jedem
künftigen Release müssen dessen eigene Prüfungen bestehen; das grüne Ergebnis
von 0.4.3 gilt nicht für spätere Änderungen. Details: [Updateablauf](automatic-updates.md).
