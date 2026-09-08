# Wiederherstellung des v0.4.1-GUI-Designs

## Ziel und feste Grenze

Die sichtbare Desktop-Oberfläche orientiert sich wieder vollständig am Stand
`v0.4.1`: Kartenlayout, Farben, Abstände, Typografie, Kopfzeile, Suchfilter,
gestapelte Detailseiten, Viewer, Kundenmasken, Einstellungs-Popup und
Indexserver-Tray. Die Paket- und Prozessgrenzen von 0.4.2 bleiben davon
unberührt.

- Der Client liest ausschließlich lokale, geprüfte Generationen.
- Der Client enthält keinen Index-Writer und keine Kundenerkennung.
- Index-, Erkennungs- und administrative Aktionen laufen ausschließlich über
  die authentifizierte Server-API.
- Offline-Änderungen bleiben im Client-Overlay und in den Outboxes; ein
  Konflikt überschreibt nie still einen Serverstand.

Das frühere Erscheinungsbild ist damit eine reine Präsentationsschicht über
den neuen Client-Ports. Es ist keine Rückkehr zur monolithischen Architektur.

## Wiederhergestellte Oberfläche

### Hauptfenster

- ursprüngliche Fenstergeometrie `1400 × 900`, Mindestgröße `920 × 640`
- durchgehende Suchkopfzeile mit Suchhistorie, `Suchen`, Filter-Popup und
  Zahnrad-Schaltfläche
- Suchergebnisse als getrennte Kartenbereiche für Kunden und Ordner;
  Datei- und Inhaltstreffer sind vorerst bewusst ausgeblendet
- Navigation ohne sichtbare Haupt-Tabs über Such-, Kunden- und Ordnerseite,
  einschließlich `Alt+Links` und `Alt+Rechts`
- Kunden- und Ordnerdetailseite im responsiven Zwei-Karten-Splitter
- ursprüngliche interne PDF-, Office-, Tabellen-, Text- und Bildvorschau
- dauerhafte Statuskarte am unteren Fensterrand
- Hauptfenster-Traymenü mit Zugriff auf den eigenständigen Indexserver-Tray

### Einstellungen

- eigenständiger Ersteinrichtungsdialog im ursprünglichen modalen Aufbau mit
  `Willkommen`, Erklärung, Pfadzeile, `Durchsuchen`, Inline-Fehler,
  `Später` und `Einrichtung abschließen`
- Ersteinrichtung erfasst ausschließlich Clientwerte: Server-URL,
  Client-Token, `source_id` und den lokalen Pfad der aktuellen Plattform; sie
  startet weder einen Indexlauf noch greift sie auf Serverdateien zu
- zentriertes, abgerundetes Popup mit linker Navigation
- die ursprünglichen Bereiche `Allgemein`, `Indexierung`, `Suche`,
  `Kundenerkennung`, `Statistik` und `Aussehen`
- Clientwerte wie Serververbindung, lokale Quellzuordnungen und
  Synchronisationsintervall sind in das alte Seitenraster eingeordnet
- die alte Statistik-Karte zeigt echte Kennzahlen aus der lokalen Kundenkopie
  und dem aktiven Suchkatalog; die 0.4.2-Konfigurationskarte bleibt darunter
- `Indexserver öffnen` startet über ein GUI-Signal nur den eigenständigen
  Tray-Prozess
- serverseitige Indexoptionen bleiben im eigenständigen Admin-Tray
- Umgebungsvariablen werden weiterhin als nicht überschreibbare Quelle
  sichtbar gemacht

### Indexserver-Tray

- ursprüngliche Kopfzeile mit farbigem und bei einem Lauf animiertem
  Serverstatus
- Tabs `Übersicht`, `Indexeinstellungen` und `Aktivität`
- getrennte Karten für Server, Indexjob, Dokumentinhalte und veröffentlichte
  Generation
- vollständige serverseitige Indexkonfiguration und Wartungsaktionen
- Wartungsaktionen sind nach erfolgreicher Client-Token-Verbindung direkt bedienbar

## 0.4.2-Funktionen und erforderliche GUI-Einbindung

Die folgende Liste ist zugleich die Abnahmeliste für den Design-Rückbau. Sie
trennt Funktionen, die im wiederhergestellten Design bereits bedienbar sind,
von Funktionen, für die das alte 0.4.1-Layout keine vollständige Darstellung
besaß und deshalb noch eine eigene UX benötigt.

`UI` bedeutet, dass die technische Funktion bereits vorhanden ist und nur eine
neue sichtbare Bedienung benötigt. `Application/API + UI` kennzeichnet Punkte,
die nicht durch bloßes Reaktivieren einer alten Schaltfläche sicher umgesetzt
werden können, weil zuerst ein passender revisionierter Vertrag fehlt.

| Funktion aus 0.4.2 | Stand im wiederhergestellten Design | Noch erforderlich |
|---|---|---|
| globale Suche über Kunde, Projekt und Ordner | in den alten Ergebnis-Karten integriert; Unterordner optional, Datei-/Inhaltstreffer vorerst deaktiviert | **UI:** Seitennavigation sowie Trefferart- und Kundenfilter ergänzen, bevor die Dateisuche später bewusst reaktiviert wird |
| Facetten für Bereich und Jahr sowie Sortierung | altes Filter-Popup verwendet die neuen Facetten; der derzeit bedeutungslose Dateitypfilter ist ausgeblendet | **UI:** expliziten `source_id`-Filter ergänzen |
| portable Pfade (`source_id` + relativer Pfad) | vor jedem nativen Öffnen lokal aufgelöst | **UI:** Test-/Erreichbarkeitsanzeige je Mapping ergänzen |
| getrennte Index- und Kundengeneration | Sync und atomare Aktivierung bleiben aktiv | **UI:** detaillierte lokale Generations-, Alters- und Prüfsummenansicht ergänzen |
| aktiver Stand plus drei lokale Backups | unverändert in der Generation-Ablage; serverseitige Anzahlen stehen im Tray | **UI:** lokale Vorgänger sowie Retention-Warnung und vorhandenes `retry_retention()` visualisieren; **API + UI:** gezielte Wiederherstellung eines Serverbackups ergänzen |
| Start-, Intervall- und manuelle Synchronisation | Start und Intervall laufen automatisch; der manuelle Sync-/Replay-Pfad ist vorhanden | **UI:** sichtbare Aktion für manuellen Sync/Outbox-Replay ergänzen; **Application + UI:** nächsten Fälligkeitszeitpunkt modellieren und anzeigen |
| vollständiger Offline-Start | letzter gültiger Stand bleibt durchsuchbar | Alter der lokalen Generation deutlicher anzeigen |
| Offline-Kunden-Outbox | Zähler, Status und Konfliktdialoge sind angebunden | **UI:** eigene Warteschlangenansicht mit einzelnen Operationen ergänzen |
| Offline-Journal-Outbox | alte Journalansicht und Konfliktauflösung sind angebunden | **UI:** Reihenfolge und Abhängigkeiten blockierter Folgeänderungen sichtbar machen |
| optimistische Revisionen und HTTP 409 | Neuladen, Zusammenführen, Wiederholen und Verwerfen bleiben erhalten | **UI:** Feld-für-Feld-/Drei-Wege-Vergleich Server/Lokal im Merge-Dialog ergänzen |
| Idempotency-Keys | vollständig im Application-Layer, ohne Bedienbedarf | optional technische Diagnoseansicht ergänzen |
| serverseitige Kundenerkennung | Review und globaler Start bleiben im Server-Tray; die letzten Läufe werden bereits geladen | **UI:** vollständige Historie der Erkennungsläufe statt nur des jüngsten Zeitpunkts ergänzen |
| Erkennungsregeln und Dokumentmuster | Mindestjahr und bevorzugte Dokumentmuster sind im Tray editierbar | **API + UI:** alte Aktivierung, Häufigkeitsschwelle, Blacklists und Blacklist-Vorschläge ergänzen |
| dokumentbasierte Kundenvorschläge | alte Vorschlagskarten nutzen die v2-API; Kontaktvorschläge zeigen Name, Rolle, E-Mail und Telefon sowie Abweichungen von manuell gepflegten Kontakten | **API + UI:** kundenbezogenen „neu suchen“-Endpunkt und frei bestätigbaren Wert, etwa für den Kundentyp, ergänzen |
| serververwaltete Projektzuordnung | Projekte sind im alten Dienstleistungsbereich und im Dateien-Tab des Kundeneditors in den beiden Listen `Gefundene mögliche Ordner`/`Ausgewählte Ordner` lesbar; die alte Zuordnungsbedienung ist sichtbar und eindeutig deaktiviert | **API + UI:** revisionierte manuelle Zuordnung, Umordnung, Entfernung und Zuordnung eines noch freien Ordners ergänzen |
| Erkennungsfall gemeinsam/getrennt anlegen und Kundentyp bestätigen | gemeinsam anlegen, zuordnen und ablehnen sind angebunden; alte Zusatzfelder bleiben sichtbar | **API + UI:** transaktionalen Split-Endpunkt und Kundentyp im Entscheidungsvertrag ergänzen |
| kennwortfreie Serversteuerung | alle Aktionen verwenden ausschließlich den Client-Token | keine |
| Serverstatus, Jobfortschritt und Generation | alte Übersichtskarten sind angebunden | **API + UI:** Server-Logstream und Worker-Zuordnungen/-Fortschritt statt nur lokal beobachteter Statusereignisse ergänzen |
| Indexlauf, Vollaufbau, Abbruch, Löschen, Neustart | alte Wartungsaktionen rufen ausschließlich v2-Admin-API auf | keine |
| vollständige OCR-/Inhalts-/Ressourceneinstellungen | altes Einstellungsraster im Tray ist angebunden | keine |
| zusätzliche alte Inhaltsjob-Wartung | grundlegender Inhaltsaufbau gehört zum normalen Server-Indexlauf | **API + UI:** Pause/Fortsetzen, nur fehlgeschlagene Inhalte wiederholen und Optimieren ergänzen, falls diese getrennte Bedienung weiterhin gewünscht ist |
| Server- und Clienttoken aus sicherer Konfiguration | bestehende Client-Einstellungen bleiben angebunden | **API + UI:** Token-Rotation als geführten Ablauf ergänzen |
| Serververbindung und Clientkonfiguration | eigener alter Ersteinrichtungsdialog sowie Einstellungs-Popup; beide speichern über denselben Client-Presenter, der unabhängige Tray lässt sich dort öffnen | **UI:** dedizierten Verbindungstest ergänzen |
| Client- und Indexstatistik | echte lokale Kunden-/Suchkennzahlen im alten Statistikbereich, Live-Indexwerte im Tray | optionales Query-ViewModel für zusätzliche serverseitige Laufzeitkennzahlen ergänzen |
| alte lokale Suchoptionen | die Suchgeneration ist unveränderlich und serververwaltet | **Application/API + UI:** Volltext-Schalter und konfigurierbare Maximalergebnisse neu entwerfen; lokale parallele Index-Shards werden bewusst nicht wieder eingeführt |
| v1-Kompatibilitätsadapter und `/v2/system/info` | v1 arbeitet transparent unterhalb der GUI; der Server veröffentlicht v2-Fähigkeiten | **Gateway + UI:** verwendete API-Version und Fähigkeiten in einer Diagnoseansicht anzeigen |
| eigenständiger Tray-Prozess | bleibt unabhängig vom Hauptfenster start- und beendbar | keine |

## Bewusst nicht zurückgebrachte monolithische Aktionen

Die folgenden alten Beschriftungen dürfen optisch nur als Weiterleitung zum
Server vorkommen, nicht wieder als lokale Implementierung:

- lokalen Index erstellen oder löschen
- lokalen Dateisystemmonitor zum Indexieren starten
- OCR oder Kundenerkennung im Hauptprozess ausführen
- `customers.db` direkt aus der GUI verändern
- Docker über Dateizugriffe oder Shell-Aufrufe aus dem Produktionsclient
  steuern

Wo dafür bereits eine v2-API existiert, führt die alte Schaltfläche zum
Admin-Tray oder ruft den entsprechenden Server-Port auf. Fehlt eine passende
API, erklärt die Oberfläche den serververwalteten Zustand, statt eine
scheinbar funktionierende lokale Aktion anzubieten.
