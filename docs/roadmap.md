# Offene Produktarbeit

Stand: 14. September 2026. Aktuelle Version: Client/Server/Contracts 0.4.2.
Die verbindliche Freigabeliste steht in [Releasevorbereitung](releasing.md).
Diese Liste enthält spätere Produktarbeit, keine zugesagten Releasefunktionen.

- Konfliktzusammenführung, Projektzuordnung, Diagnose und Wiederherstellung gemäß
  [GUI-Migrationsmatrix](gui-design-restoration.md) vervollständigen.
- Flatpak für weitere Linux-Distributionen; zusätzliche ARM-Desktopziele nach Bedarf.
- Signierte automatische Updates mit Rollback-Konzept.
- Synology-Dauerbetrieb, Volume-Ausfall und Backup-/Restore-Monitoring abnehmen.
- Rollen und feinere API-Berechtigungen statt des gemeinsamen Client-Bearer-Tokens.
- Mehrere logisch benannte Datenquellen.
- IMAP mit Zugangsdaten im OS-Keyring, serverseitige E-Mail- und Anhangindizierung.
- Drag-and-drop, vollständige Tastaturführung und Screenreader-Abnahme.

Sperrliste, gesammelte Entscheidungen, lokale Dokumentauslesung, gezielte
Neubewertung, vollständiger Erkennungsneuaufbau und Workeranzeige sind bereits
implementiert. Fachliche Abnahme an einem unabhängig bewerteten Referenzbestand
und Laufzeitmessungen auf der Zielhardware stehen weiterhin aus.

Die alten Umsetzungs- und Ideenlisten sowie die Prüfberichte vom 9. September
wurden zur Releasevorbereitung aus dem aktuellen Dokumentationsbaum entfernt.
Ihre historischen Fassungen bleiben in der Git-Historie erhalten. Für aktuelle
Zusagen gelten die Bedienungsdokumentation und die Tests des Releasecommits.
