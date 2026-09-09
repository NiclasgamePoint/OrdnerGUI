# Produktideen nach der Client-/Server-Trennung

Diese Liste enthält unverbindliche Ideen. Der verbindliche technische Stand
steht in [Umsetzungsplanung.md](Umsetzungsplanung.md).

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

Noch neu einzubauende Features
Technisch bereits vorhanden, aber noch ohne vollständige Bedienoberfläche:
- Such-Pagination sowie Trefferart-, Kunden- und source_id-Filter
- Erreichbarkeitstest für lokale Pfadzuordnungen
- lokale Generations-, Alters-, Prüfsummen- und Backupansicht
- sichtbarer manueller Sync und einzelnes Outbox-Replay
- detaillierte Kunden-/Journal-Warteschlange
- Drei-Wege-Feldvergleich bei Kundenkonflikten
- vollständige Historie der Erkennungsläufe
- sichtbarer Admin-Sitzungsstatus, Ablaufzeit und Sperren-Aktion
- API-Versionen und Serverfähigkeiten als Diagnoseansicht
- optionale Idempotenz-Diagnose
Diese alten Funktionen brauchen zuerst neue Serververträge:
- Projektordner manuell zuordnen, entfernen oder verschieben
- noch nicht zugeordneten Ordner einem Kunden geben
- Kontaktdaten für einen einzelnen Kunden neu suchen
- abweichenden Vorschlagswert ausdrücklich übernehmen
- Erkennungsfall „Getrennt anlegen“ und Kundentyp übertragen
- Erkennungsschwelle, Blacklists und Blacklist-Vorschläge
- Server-Logstream und Worker-Detailfortschritt
- bestimmtes Serverbackup auswählen und wiederherstellen
- Client-Token rotieren
- Inhaltsjob pausieren, fortsetzen, Fehler wiederholen oder optimieren
- Volltext-/Maximalergebnis-Konfiguration neu entwerfen
Die dazugehörigen alten Bedienelemente sind teilweise sichtbar, aber eindeutig deaktiviert. Es wurde keine lokale Ersatzlogik eingebaut.