# Produktideen nach der Client-/Server-Trennung

Diese Liste enthält unverbindliche Ideen, Stand 9. September 2026. Implementierte
Funktionen und offene Abnahmen stehen in
[Umsetzungsplanung.md](Umsetzungsplanung.md); die aktuelle Bedienoberfläche ist
in der [GUI-Migrationsmatrix](docs/gui-design-restoration.md) dokumentiert.

## Client

- Native, signierte Installer und automatische Updates
- Verbesserte feldweise Konfliktzusammenführung
- Drag-and-drop von Dokumenten
- Erweiterte Barrierefreiheit und Fokusführung

## Server

- Synology-Dauerbetrieb mit Monitoring und Benachrichtigungen
- Mehrere logisch benannte Datenquellen
- Rollen und feinere API-Berechtigungen
- IMAP-Abruf, serverseitige E-Mail-Indizierung und Kundenzuordnung

## Betrieb

- Externes Backup-/Restore-Monitoring
- Signierte Containerimages mit SBOM
- Optionales Web-Dashboard für reine Administration


---

## Abgrenzung zum bereits umgesetzten Stand

Die serverweite Sperrliste, gesammelte Änderungen mit Bestätigung, die gezielte
Neubewertung und Dokumentneuauslesung einzelner Kunden, der vollständige
Erkennungsneuaufbau sowie die Anzeige von Worker-Summen sind bereits vorhanden.
Sie werden deshalb nicht mehr als neue Funktionen geführt.

Adminpasswort, Adminsitzungsstatus und Ablaufzeit sind keine offenen
Oberflächenaufgaben: Das frühere Sitzungsmodell wurde entfernt. Ein künftig
feineres Rollenmodell benötigt eine eigenständige Konzeption.

Noch fehlende Bedienelemente und Serververträge werden ausschließlich in der
[GUI-Migrationsmatrix](docs/gui-design-restoration.md) gepflegt. Dazu zählen je
nach Funktionsbereich zusätzliche Konflikt-, Diagnose-, Projektzuordnungs- und
Wiederherstellungsaktionen; sichtbare Platzhalter sind keine ausführbaren
Funktionen.
