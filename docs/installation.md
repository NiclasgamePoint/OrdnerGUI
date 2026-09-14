# PapaGUI installieren

Stand: Releasevorbereitung 0.5.0. Installer werden zunächst als **unsignierte
Testartefakte** gebaut. Ein öffentlicher, signierter Release ist noch nicht
nachgewiesen. Freigegebene Downloads dürfen erst nach der
[Releaseabnahme](releasing.md) verlinkt werden.

## Desktopclient

Der Installer enthält Python, Qt, den Client und die Indexserver-Verwaltung
(Tray). Endanwender müssen weder Python noch pip installieren. Der Tray ist
eine Bedienoberfläche für einen separaten Server, kein lokaler Indexserver.

| Ziel | Installer | Installation | Geplante Abnahmebasis |
| --- | --- | --- | --- |
| Windows x64 | `*-windows-x64-setup-unsigned.exe` | Doppelklick; Installation für den aktuellen Benutzer, Startmenü und Deinstallation | Windows 11; Windows 10 1809+ technisch durch Qt unterstützt, gesondert abzunehmen |
| macOS Intel | `*-macos-x64-unsigned.pkg` | Doppelklick; beide Apps unter `/Applications/PapaGUI` | macOS 15 Intel |
| macOS Apple Silicon | `*-macos-arm64-unsigned.pkg` | Doppelklick; beide Apps unter `/Applications/PapaGUI` | macOS 14 ARM64 |
| Linux x64 | `*-linux-x64.deb` | Paketverwaltung oder `sudo apt install ./papagui-client-0.4.3-linux-x64.deb` | Ubuntu 24.04 mit Desktop |

Diese Ziele sind vorbereitet, nicht pauschal auf allen Geräten abgenommen.
Qt 6.11 unterstützt macOS ab 13; die tatsächliche Untergrenze eines konkreten
Bundles ist zusätzlich von Python und dem nativen Build abhängig. Das Linuxpaket
setzt glibc >= 2.39 und die deklarierten Desktopbibliotheken voraus. Es ist keine
Zusage für ältere Ubuntu-Versionen, alle Debian-Versionen, Fedora oder Arch.
Für weitere Distributionen ist **Flatpak** die empfohlene nächste Ausbaustufe.
Windows ARM64 und Linux ARM64 sind noch keine nativen Clientziele.

Für die öffentliche Verteilung sind Windows-Signaturen sowie Apple Developer
ID Application/Installer, Notarisierung und Stapling vorgesehen. Eine gültige
Windows-Signatur garantiert bei neuen Builds noch keine sofortige Freiheit von
SmartScreen-Warnungen. Diese Zertifikate sind nicht im Repository enthalten.
Signierungskosten ändern nichts daran, dass PapaGUI kostenlos und freie Software
sein soll. Konkrete Voraussetzungen und noch offene Arbeiten stehen unter
[Beta-Abnahme und Signierung](beta-readiness.md).

Quellen: [Qt-Plattformen](https://doc.qt.io/qt-6/supported-platforms.html),
[Apple-Notarisierung](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
[PyInstaller-Plattformgrenzen](https://pyinstaller.org/en/stable/operating-mode.html).

## Erster Start

1. Den Server einmalig gemäß [Betriebsanleitung](server/operations.md) installieren.
2. PapaGUI öffnen und Server-URL sowie den Client-API-Token eintragen.
3. Den lokalen Pfad zur Datenquelle zuordnen und die erste Synchronisation prüfen.
4. Verbindung trennen und den letzten synchronisierten Stand offline prüfen.

Der Desktopclient benötigt keinen Docker-Daemon. Für gemeinsame Indexierung muss
ein Server erreichbar sein, beispielsweise auf einem NAS oder anderen Rechner.
Optional können Server und Client auf demselben PC laufen; der Server benötigt
dann Docker Engine/Compose beziehungsweise Docker Desktop und eine konfigurierte
Datenquelle. Die Entwickler-Startskripte sind kein Endanwenderinstaller.

## Aktualisierung und Entfernung

Ab 0.5.0 können installierte Clients und ein eigens eingerichteter Server-Updater
neue stabile GitHub Releases automatisch übernehmen. Der erste Wechsel von 0.4.3
benötigt einmalig den neuen Installer. Ablauf, Sicherungen, private Repositories
und Hosteinrichtung stehen unter [Automatische Updates](automatic-updates.md).

Windows: neuen Installer über die vorhandene Version installieren. Entfernung
über die Windows-Appverwaltung. Einstellungen und Offline-Daten liegen außerhalb
des Programmordners und werden nicht vom Uninstaller gelöscht.

macOS: neues Paket installieren; Client und Tray bleiben zusammen. Zur Entfernung
den Ordner `/Applications/PapaGUI` im Finder löschen. Ein Apple-Paket besitzt
keinen eingebauten Uninstaller; der Paketbeleg kann zusätzlich durch eine
Administration entfernt werden. Benutzerdaten liegen separat.

Linux: neue `.deb` mit `apt install` installieren; `sudo apt remove papagui-client`
entfernt das Programm. Benutzerdaten bleiben erhalten.

Vor Änderungen am Server dessen Daten- und Konfigurationsvolumes sichern und
[Migrationshinweise](data-and-migrations.md) beachten. Ein Clientupdate ersetzt
keine Servermigration. Der Installer startet weder einen Indexneuaufbau noch
löscht er Dokumente, Tokens oder Serverdaten.

## Lizenzhinweise

PapaGUI steht unter GPL-3.0-or-later. Die mitgelieferten Hinweise liegen unter
`licenses/` im Windows-/macOS-Programmordner beziehungsweise unter
`/opt/papagui/licenses` auf Linux. Dort bleiben sie auch erhalten, wenn eine
minimale Linuxinstallation gewöhnliche Systemdokumentation ausfiltert. Weitere Angaben:
[Lizenztext](../LICENSE), [Fremdsoftware](../THIRD_PARTY_NOTICES.md).
