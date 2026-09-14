# SignPath-Bewerbung: vorbereitete Angaben

Stand: 14. September 2026. Noch nicht eingereicht. Die Repositorysichtbarkeit
wurde über GitHub als öffentlich geprüft; MFA ist laut Projektinhaber aktiv.
Eine SignPath-Zusage und ein Signierungszugang fehlen noch. Den tatsächlichen
Status der Downloads für 0.5.0 vor Absenden unter GitHub Releases prüfen.

## Erledigt und vorbereitet

| Punkt | Nachweis |
| --- | --- |
| Öffentliches Repository | https://github.com/NiclasgamePoint/OrdnerGUI |
| GitHub-MFA | Vom Projektinhaber bestätigt |
| Verantwortung | `NiclasgamePoint`: Entwicklung, Reviews und manuelle Signierungsfreigabe bestätigt |
| Logovorlage | ChatGPT-Erzeugung ohne weitere Vorlagen und öffentliche Weitergabe vom Inhaber bestätigt |
| Eigener Projektcode | Laut Inhaber von ihm oder in seiner Codex-gestützten Entwicklung erstellt |
| GitHub-Support-Anfrage | Laut Projektinhaber eingereicht; Bestätigung der serverseitigen Bereinigung steht aus |
| Projektlizenz | GPL-3.0-or-later; Paketversionen 0.5.0 konsistent |
| Windows-Metadaten | Client, Tray und Setup lokal mit Produktname PapaGUI und Version 0.5.0 gebaut/geprüft |
| Paket-Lizenzsammlung | 12 erfasste Python-/Buildpakete, 17 Lizenzdateien; LICENCE-Schreibweise korrigiert |
| Lokale Prüfungen | 1.244 Tests, 94 % Coverage; Installation, Upgrade und Deinstallation unter Windows geprüft |
| Geprüfter Anwendungsstand | Tag `0.5.0`, Commit `4df73ba`; Quality, native Installer und Serverbuild erfolgreich |
| GitHub-Installerbuild für 0.5.0 | https://github.com/NiclasgamePoint/OrdnerGUI/actions/runs/34865795525 |
| Veröffentlichung und aktuelle Downloadprüfung | https://github.com/NiclasgamePoint/OrdnerGUI/actions/runs/34867004631 |

Die Nachweise beziehen sich auf den tatsächlich getaggten Anwendungsstand 0.5.0.
Windows- und Linux-Installer bestehen auch im Releaseworkflow; Windows benötigte
wegen eines PowerShell-Timeouts einen gezielten Wiederholungslauf. Das veröffentlichte
Serverimage ist für AMD64 und ARM64 anonym abrufbar. Beide Debian-Quellenexporte
erfassen jeweils 183 installierte Pakete. Vor dem Absenden im oben verlinkten
Releaseworkflow und auf der Downloadseite prüfen, dass die Veröffentlichung
vollständig abgeschlossen ist. Die Installer sind ausdrücklich unsigniert.

Die Veröffentlichungskorrektur auf `main` schließt zusätzliche `.dockerbuild`-
Diagnoseartefakte aus dem normalen ZIP-Download aus. Der bereits veröffentlichte
Tag und seine Programmdateien bleiben diesem ursprünglichen Anwendungsstand
zugeordnet. Frühere Prüfschritte sind im
[Vorbereitungsprotokoll](development/signpath-preparation.md) dokumentiert.

## Vor dem Absenden noch klären

1. **Kontakt:** `NiclasgamePoint` übernimmt die bestätigten Rollen. Kontaktadresse
   direkt im Bewerbungsformular eintragen; sie muss nicht hier veröffentlicht werden.
2. **Rechte:** Herkunft des eigenen Codes und Freigabe der Logovorlage sind
   bestätigt. Fremdbibliotheken werden separat im nativen Lizenzinventar erfasst.
3. **Historische Dokumente:** Die zwei PDFs wurden auf ausdrücklichen Wunsch aus
   allen öffentlichen Branches und Tags sowie dem lokalen Git-Speicher entfernt.
   GitHubs interne Verweise der Pull Requests #1 und #2 sind noch betroffen;
   die Cache-/Referenzbereinigung wurde laut Projektinhaber bei GitHub Support
   angefragt. Die Erledigungsbestätigung steht noch aus. Der private Anfragetext
   liegt unter `build/signpath-audit/github-support-request.md`.
4. **Binärlieferumfang:** 0.5.0 ergänzt Inventare der tatsächlichen nativen
   PyInstaller-Dateien, vollständige Qt/PySide-Quellarchive mit Prüfsummen und
   einen versionsgenauen Debian-Quellenexport aus dem Release-Serverimage.
   Die [Plattformprüfung](native-license-review.md) und reale Installation abnehmen.
5. **Öffentliche Unterlagen:** Richtlinie, Datenschutzhinweise und Anleitung gehören
   zum Stand 0.5.0. Die unten genannten Links vor Absenden auf Erreichbarkeit prüfen.
6. **Öffentlicher Testrelease:** 0.5.0 verwendet einen numerischen Tag und wird
   ausdrücklich als unsignierte Beta behandelt. Den Release-Link erst verwenden,
   wenn der öffentliche Pre-release tatsächlich fertige geprüfte Downloads enthält.

SignPath erwartet unter anderem bereits veröffentlichte Software sowie klare
Verantwortlichkeiten und nachvollziehbare Builds. Ein Antrag kann erst nach
deren tatsächlicher Vorbereitung als vollständig bezeichnet werden.
[Offizielle Bedingungen](https://signpath.org/terms.html).

## Formularangaben

| Feld / Zweck | Vorbereiteter Inhalt |
| --- | --- |
| Projektname | PapaGUI |
| Repository / Quellcode | https://github.com/NiclasgamePoint/OrdnerGUI |
| Lizenz | GPL-3.0-or-later |
| Sprache / Build | Python 3.11, PySide6, PyInstaller, Inno Setup 6 |
| Plattform für die Signierung | Windows x64 |
| Programme | papagui-client.exe, papagui-tray.exe, Inno-Uninstaller und Setup-EXE |
| Herkunft | GitHub Actions, GitHub-hosted Windows runner |
| Verantwortlicher / Kontakt | `NiclasgamePoint`: Entwicklung, Reviews, Signierungsfreigabe; Kontakt direkt im Formular |
| Download / Release | https://github.com/NiclasgamePoint/OrdnerGUI/releases/tag/0.5.0 — erst nach fertigen Downloads verwenden |
| Code signing policy | https://github.com/NiclasgamePoint/OrdnerGUI/blob/main/CODE_SIGNING.md |
| Datenschutz | https://github.com/NiclasgamePoint/OrdnerGUI/blob/main/PRIVACY.md |

Die tatsächlichen Felder auf der Bewerbungsseite können anders heißen.
Diese Liste ist eine Ausfüllhilfe, keine Behauptung über ein unveränderliches Formular.

## Englischer Projekttext zum Kopieren

```text
PapaGUI is a free and open-source desktop document catalog, licensed under
GPL-3.0-or-later, with a separate self-hosted indexing server.

The Windows installer contains two PySide6 applications: the desktop client
and an independent server-control tray. Documents are processed on the
operator's configured server. No external AI service is used for document
processing. Installed clients check GitHub for stable software updates;
this network behavior and the available controls are documented separately.

We are preparing our first Windows and Ubuntu beta. macOS distribution is
deferred. Windows binaries are built with PyInstaller and packaged with
Inno Setup 6. Our repository is public and the project owner has enabled
GitHub MFA.

We would like to use SignPath Foundation to sign the two application EXEs,
the generated Inno Setup uninstaller, and the final setup executable.
We can build on GitHub-hosted runners and manually approve signing requests.

Please confirm the appropriate artifact configuration and signing sequence
for PyInstaller onefile executables and the Inno Setup uninstaller, including
the treatment of embedded open-source components and product/version metadata.

Repository: https://github.com/NiclasgamePoint/OrdnerGUI
Public test release: [insert the actual published release URL]
Signing policy: [insert the verified public policy URL]
Privacy information: [insert the verified public privacy URL]
Maintainer, reviewer and signing approver: NiclasgamePoint
```

## Danach durch den Projektinhaber

Nach Erledigung der offenen Punkte die
[offizielle Bewerbung](https://signpath.org/apply.html) im Browser öffnen,
Angaben übernehmen und selbst absenden. Eine Zusage oder Ablehnung kommt von
SignPath. Danach genügt zunächst die Rückmeldung über den Status und die
nicht geheimen Projektkennungen. Tokens nur direkt als GitHub-Secrets speichern.

Weitere Schritte: [vollständige Windows-Signierungsanleitung](windows-signing.md).
