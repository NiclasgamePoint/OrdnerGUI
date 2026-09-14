# Code signing policy

Status: Entwurf zur Bewerbung bei SignPath Foundation, 14. September 2026.
Eine Aufnahme oder kostenlose Signierungsleistung wurde noch nicht bestätigt.
Aktuell angebotene Testinstaller sind unsigniert, sofern das konkrete Artefakt
nicht ausdrücklich mit einem geprüften Signierungsnachweis veröffentlicht wird.

## Bestätigte Verantwortlichkeiten

Der Projektinhaber hat am 14. September 2026 bestätigt, dass der GitHub-Account
[NiclasgamePoint](https://github.com/NiclasgamePoint) die Verantwortung für
Entwicklung, Code-Reviews und die manuelle Freigabe von Signierungsanfragen
übernimmt. GitHub-MFA ist laut Projektinhaber aktiv. Weitere Personen sind
derzeit nicht als Signierungsfreigeber benannt.

Die Rollen in SignPath werden erst nach Aufnahme des Projekts eingerichtet.
Diese Bestätigung stellt noch keinen Signierungszugang dar.

## Vorgesehener Ablauf

Signiert werden ausschließlich geprüfte PapaGUI-Builds aus diesem Repository.
Anwendungs-EXEs, eingebetteter Uninstaller und finaler Installer werden getrennt
berücksichtigt. Ein Release benötigt eine ausdrückliche Signierungsfreigabe.
Der Build scheitert bei fehlender oder ungültiger angeforderter Signierung.
Quelltag, fertige Downloads und Prüfsummen müssen zusammenpassen.

Nach einer Aufnahme werden die zutreffenden SignPath-Danksagungen und der
eingerichtete Signierungsablauf hier ergänzt. Der Entwurf behauptet keine bereits
bestehende Unterstützung. Verbindlich sind dann auch die mit SignPath
abgestimmten Artefakt- und Herkunftsregeln.

Netzwerkverhalten: [Datenschutzhinweise](PRIVACY.md).
Einrichtung: [Windows-Signierungsanleitung](docs/windows-signing.md).
