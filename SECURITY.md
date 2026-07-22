# Security-Konzept (Mindestmassnahmen)

Dieses Dokument beschreibt die aktuell vereinbarten Mindestmassnahmen fuer OrdnerGUI.

## Ziele

- Vertraulichkeit von Kundendaten und Metadaten.
- Nachvollziehbarkeit von automatischen Zuordnungen.
- Robuster Betrieb ohne stilles Ueberschreiben sicherheitsrelevanter Daten.

## Bedrohungsbild (kurz)

- Unbeabsichtigte Fehlzuordnung von Kundenordnern (Datenvermischung).
- Zugriff auf lokale Datenbanken durch falsche Dateirechte.
- Ausfuehrung externer Tools (z. B. Konverter, ripgrep) mit ungueltigen Eingaben.
- Potenziell unsichere Update- oder Deployment-Pfade.

## Umgesetzte Mindestmassnahmen

- SQL-Injection-Schutz in dynamischen Schema-Helfern durch Identifier-Validierung.
- Automatische Kundenzusammenfuehrung nur noch bei hochkonfidenten Metadaten.
- Ordner vor 2016 werden fuer automatische Kundenerstellung ausgeschlossen.
- Hintergrundindexing laeuft entkoppelt, inkl. kontrollierter Abbruch-Signale.

## Vorgaben fuer Secrets und Konfiguration

- Keine Zugangsdaten im Klartext im Repository.
- Nutzernahe Einstellungen in QSettings nur fuer nicht-sensitive Werte.
- Fuer kuenftige Mail-Anbindung: Zugangsdaten ausschliesslich ueber OS-Keyring speichern.

## Dateisystem- und Laufzeitrechte

- Datenbanken und Statusdateien nur mit Nutzerrechten der App ausfuehren.
- Schreibriffe auf Index- und Kundendaten auf den konfigurierten Datenpfad begrenzen.
- Externe Programme nur mit expliziten Dateipfaden aus vertrauenswuerdigen Quellen aufrufen.

## Logging und Datenschutz

- Keine sensiblen Inhalte (z. B. komplette Dokumentinhalte, Secrets) in Logs schreiben.
- Fehlertexte auf das noetige Minimum begrenzen.

## Offene Hardening-Punkte

- Security-Review fuer kommende IMAP-Integration (Transport, Auth, Credential-Lifecycle).
- Release-Vertrauen fuer Auto-Update-Prozess (Signaturen/Checksums).
- Rechte-/ACL-Pruefung fuer Netzlaufwerke und NAS-Szenarien.
