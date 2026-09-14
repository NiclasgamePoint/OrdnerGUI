# Changelog

## Unreleased - 0.4.4

- Automatische, gepruefte Releaseupdates mit getrennten Programmversionen,
  Datensicherung und Rueckfall auf den bisherigen Stand.


## 0.4.3 - PreRelease Testing (2026-09-14)

- Releasevorbereitung: GPL-3.0-or-later samt Paketlizenzdateien, Fremdsoftware-
  Hinweisen und plattformspezifischem Abhängigkeitsinventar ergänzen.
- Native Installer für Windows x64, macOS Intel/ARM64 und Ubuntu 24.04 x64
  vorbereiten; CI installiert und startet die Artefakte vor dem Upload.
- Release-/Installationsdokumentation zusammenführen, alte Pläne und Prüfberichte
  aus dem aktuellen Baum entfernen und lokale Windows-Releasechecks ergänzen.
- Python-3.11-Paketgrenzentest, Qt-Testbereinigung und plattformübergreifende
  Graphify-Fingerprints korrigieren; POSIX-Skripte mit LF auschecken.

- Telefonnummernerkennung um eine konservative Formatprüfung ergänzen: keine
  Messwertreihen mit Punkten oder Dezimalkommas, keine Teiltreffer aus solchen
  Zahlen und keine unbeschrifteten kompakten Inlandsnummern. Bestehende offene
  Vorschläge mit unzulässigen Formaten werden ausgeblendet; bestätigte Werte
  und Entscheidungen bleiben erhalten.

- Zahlenfelder, Auswahllisten und Schieberegler in Client- und Indexserver-Einstellungen
  erst nach ausdrücklichem Klick per Mausrad bearbeiten. Beim bloßen Darüberfahren scrollt die Seite; nach
  Fokus- oder Tabwechsel sind die Felder wieder gegen versehentliche Änderungen geschützt.

- Einstellungen ohne blockierenden UI-Thread speichern: Konfiguration,
  Windows-Dateirechte und Darstellung werden im Hintergrund geschrieben.
  Neue Verbindungswerte und Qt-Timer werden nach einem laufenden Abgleich im
  Hauptthread übernommen; fällige Abgleiche werden nachgeholt. Unveränderte
  Designs und Katalogstatistiken werden beim Speichern nicht neu aufgebaut.

- Pfadzuordnungen in den Sucheinstellungen lesbar formatieren: inhaltsabhängige,
  verstellbare Spalten, einzeilige Pfade, passende Zeilenhöhen und vollständige
  Pfade als Tooltip; lange Tabellen lassen sich horizontal durchscrollen.

- Eigene PapaGUI-Icons für Client (rot) und Indexserver (blau): transparente Motive ohne
  Schrift für Fenster, Dialoge und Tray, getrennte Windows-Taskleistenkennungen
  sowie mehrstufige ICO- und ICNS-Dateien für ausführbare Builds und macOS-Bundles.

- Telefonnummern aus Excel-Dateien von der Kundendatenerkennung ausschließen,
  einschließlich beschrifteter Telefonspalten und Ansprechpartner-Vorschlägen.
  E-Mail-Adressen und andere Angaben bleiben nutzbar. Erneute Prüfung zieht alte
  Excel-Telefonbelege zurück; bestätigte Kundendaten bleiben erhalten.

- Word-Vorschau unter Windows ohne zusätzliche Konsolenfenster: Office-Prüfung
  und Konvertierung starten wie die übrigen Vorschauhelfer ohne Konsole.

- Dateianzahlen in Projektsuchergebnissen aus dem Katalog übernehmen; Ordnersummen
  berücksichtigen Unterordner, die Gesamtstatistik zählt Dateien nur einmal.
  Fehlende Zähler aus älteren Katalogen erscheinen als unbekannt statt als null.
- Windows-Komfortstart speichert erzeugte Verbindungswerte als bearbeitbare
  Clientkonfiguration. Gespeicherte NAS-Verbindungen bleiben beim Neustart erhalten
  und lösen keinen lokalen Docker-Start aus; explizite Umgebungsvariablen behalten Vorrang.

- Kundenerkennung nach dem Dateiscan beschleunigt: Dokumentrangfolge einmal
  berechnen und Texte direkt über ihre FTS-Zeilen laden. Eigenen Fortschritt für
  Kundengruppen und Dokumentprüfung anzeigen; Statusabfragen bleiben auch während
  der Veröffentlichung erreichbar.

- Katalogaufbau beschleunigt: Volltextänderungen über indizierte Zeilenverweise,
  transaktionale Cache-Größenzähler und gebündelte Schreibvorgänge; Cache-Artefakte
  werden vor den zugehörigen Katalog-Checkpoints gespeichert. Große Artefakte
  lösen frühzeitige Checkpoints aus. Wiederaufnahme und bestehende Caches bleiben
  unterstützt; synthetischen Pyinstrument-Benchmark ergänzt.

- Ersten Indexaufbau als erreichbaren Server mit ausstehender Generation anzeigen;
  automatische Wiederholungen übernehmen den fertigen Index ohne falsche Offline-Meldung.
- Mehrfache Indextray-Fenster unter Windows durch eine Instanzsperre verhindern;
  weitere Statusklicks aktivieren das bestehende Fenster.
- Windows-GUI und Tray mit `pythonw.exe` ohne dauerhafte Konsolen starten und den
  zusätzlichen Tray-Start aus dem Startskript entfernen.

- Windows-Komfortstart erstellt fehlende Server-Images und Container mit sichtbarer
  Docker-Ausgabe; Exitcodes, fehlende Startvoraussetzungen und Containerabbrüche
  werden vor dem Offline-Fallback geprüft. Native Regressionstests ergänzt.

- Windows-Testfälle für native Pfade, Toolnamen, Prozesszeilenenden und simulierte
  Plattformen korrigiert; Linux-Ressourcenprüfungen verwenden isolierte Testwerte.
  Bash-Syntaxprüfungen testen jedes Startskript und verwenden unter Windows Git Bash.
- JSON-Ausgaben der isolierten Office-/PDF-Parser gegen Windows-Codepages abgesichert,
  damit Texte mit Umlauten und anderen Unicode-Zeichen vollständig ankommen.
- GUI-Einstellungen berücksichtigen das konfigurierte Qt-Speicherformat auch unter
  Windows; Testläufe verwenden dadurch den isolierten INI-Speicher.
- Neuaufbau-Schaltfläche in kleinen Verwaltungsfenstern verkürzt, damit sie vollständig
  erreichbar bleibt.
- Coverage-Zwischenstände in ein temporäres Verzeichnis verlegt und das Zusammenführen
  bei Testfehlern und Abbruch abgesichert; im Projekt bleibt nur die Gesamtdatei.

- pytest-qt für Clientprüfungen integriert, erste Dialogregressionen auf verwaltete
  Widgets und Signalprüfungen umgestellt; Qt-freie Serverjobs bleiben getrennt.
- Pyinstrument mit einem reproduzierbaren Profiling-Aufruf für die synthetische
  Kundenerkennung ergänzt; HTML-Berichte bleiben lokal unter `profiles/`.
- Graphify-Quellgraph-Builder und Windows-Launcher ergänzt, einschließlich
  benutzergebunden verschlüsseltem Gemini-Key und synthetischem Verbindungstest.

- Strukturierte lokale Kundendatenerkennung für Telefon, E-Mail, Firma,
  Anschrift und benannte Ansprechpartner ergänzt; Datumsfilter, Rollenprüfung,
  mehrere Belege pro Vorschlag und dauerhafte Entscheidungen verbessern die
  fachliche Prüfung. Manuell gepflegte Stammdaten bleiben geschützt.
- Kundenprüfung mit gruppierten Vorschlägen, Abdeckungsstatus, Offline-Belegen
  und gezielten Aktionen zum Neubewerten beziehungsweise Neulesen ergänzt.
- Serverweite Sperrliste und vollständigen Neuaufbau der Kundenerkennung im
  eigenen Tab der Indexserver-Seite zugänglich gemacht. Ergänzungen und
  Löschungen werden gesammelt und erst nach Bestätigung gemeinsam gespeichert.
  Eine Batch-Änderung veröffentlicht einmal die Kundenkomponente und löst keinen
  Indexneuaufbau oder OCR-Lauf aus; fehlgeschlagene Veröffentlichungen bleiben
  über Neustarts wiederholbar.
- Inhaltsindexierung und Kundenerkennung nutzen dieselbe parallele
  Dokumentauslesung mit gemeinsamem Cache, begrenzter Warteschlange und einem
  Ressourcenbudget von 1–20 Workern. Die Oberfläche zeigt aktive Worker und
  zusammengefasste Dokumentzähler.
- Reguläre Folgeabgleiche überspringen unveränderte Dokumente anhand von
  Metadaten und Auslesepolicy. Die tägliche Inhaltsprüfung verwendet Hashes,
  erhält gültige Extraktionen und berücksichtigt dauerhaft den letzten
  Prüfzeitpunkt. Explizite Dokumentneuauslesung erzwingt Parser und nötige OCR.
- Windows-Erststart korrigiert: Token-Erzeugung mit Windows PowerShell 5.1
  kompatibel gemacht, Generatorfehler abgefangen und die numerische SID beim
  Setzen der Dateirechte richtig übergeben. Native Regressionstests und eine
  Anleitung für den Wechsel des Entwicklungsrechners ergänzt; die native
  Windows-Abnahme steht noch aus.
- Dokumentation für Betrieb, API, Oberfläche, Entwicklung und Paketierung mit
  dem aktuellen Code abgeglichen; historische Pläne und Prüfergebnisse als
  solche gekennzeichnet.

- Kundenart im Kundeneditor als normale Dropdown-Auswahl bedienbar gemacht:
  Klicks auf das Feld öffnen die Liste; importierte Sonderwerte bleiben erhalten.
- Wechsel von der Suche zur Kundendetailseite blockiert die Qt-Oberfläche nicht
  mehr: Projektmetadaten, Journalzustand und serverseitige Kundenvorschläge
  werden im Hintergrund geladen; doppelte Detailanfragen und veraltete
  Antworten nach einem schnellen Kundenwechsel werden verhindert.
- Volltextsuche des Desktopclients beschleunigt: SQLite wertet die FTS-Abfrage
  nicht mehr korreliert für jeden Katalogeintrag aus. Dadurch blockiert eine
  Suche in großen lokalen Generationen nicht länger die Qt-Ereignisschleife.
- Sichtbare Hauptsuche vorerst auf Kunden und Projektordner begrenzt;
  Unterordner sind über den bestehenden Filter zuschaltbar, Datei- und
  Inhaltstreffer bleiben aus der Ergebnisansicht ausgeblendet.
- Projektunterordner in der Dienstleistungsansicht lassen sich aufklappen und
  laden ihre direkten Unterordner und Dateien bedarfsgerecht aus dem lokalen
  read-only Katalog nach.

- Adminpasswort, Argon2-Abhängigkeit, Adminsession-API und Passwortdialoge
  vollständig entfernt; Servereinstellungen und Wartungsaktionen benötigen nur
  noch den automatisch verwalteten Client-Token.

- Das vollständige v0.4.1-Erscheinungsbild von Hauptfenster,
  Einstellungs-Popup, Kundenmasken, Viewer und Indexserver-Tray auf die getrennte
  0.4.2-Clientarchitektur zurückportiert; Client und Server bleiben vollständig
  getrennt und sämtliche Indexaktionen laufen weiterhin nur über die API.
- Die vollständige read-only-Katalogschnittstelle im SearchCoordinator
  freigelegt, damit die wiederhergestellte Karten-/Ordnernavigation keine
  Adapter oder Index-Writer direkt kennt.
- GUI-Migrationsmatrix für neue Offline-, Generations-, Pfad- und
  Konfliktfunktionen ergänzt.
- Statistik-Seite wieder mit lokalen Kunden- und Suchkennzahlen gespeist und
  den alten **Indexserver öffnen**-Button als reine Tray-Weiterleitung ergänzt.

## 0.4.2 - Client/Server Split

- Repository in die unabhängigen Pakete `papagui-client`, `papagui-server` und
  das interne `papagui-contracts` gegliedert; der frühere Monolith gehört nicht
  mehr zu einem Produktartefakt.
- Headless FastAPI-Server als alleinigen Writer für portable Indexgenerationen,
  Kundenerkennung und die maßgebliche `customers.db` eingeführt.
- Komponentenweise Generationsmanifeste, SHA-256-Prüfung, atomare Aktivierung
  und jeweils drei gültige Vorgängergenerationen auf Server und Client ergänzt.
- Read-only-Desktopclient mit Start-/Intervallsynchronisation, plattformneutralem
  Quellpfadmapping und vollständigem Offlinebetrieb ergänzt.
- Kundenänderungen über eine dauerhafte Outbox, Idempotency-Keys und
  Revisionskonflikte mit expliziter 409-Auflösung abgesichert.
- Eigenständiges Server-Tray mit Live-Serverstatus, Indexsteuerung und
  serverseitigen Einstellungen ergänzt.
- Leere oder vertauschte Quellmounts durch eine persistente Quellenidentität
  abgesichert und Secretdateien/Clientkonfiguration plattformgerecht gehärtet.
- Getrennte Docker-, native Client- und CI-Builddefinitionen sowie Architektur-,
  OpenAPI-, Artefakt- und Graphify-Driftprüfungen ergänzt.

## 0.4.1 - Pre-Refactor Checkpoint

- Systemtray-Indexfenster zur Docker-Serverkonsole mit Live-Status, Jobfortschritt,
  Generationen, Wartungsaktionen und kontrolliertem Containerneustart umgebaut.
- Servereinstellungen für OCR, Ressourcen, Formate, Priorisierung und Laufintervall
  aus der Haupt-GUI in die authentifizierte Indexserver-Konsole verschoben.
- Indexserver-Konsole als eigenständige, vom MainWindow unabhängige Tray-Anwendung
  mit Einmalinstanz-Aktivierung, automatischem Start durch `start.sh` und
  separatem `index-tray.sh`-Launcher ergänzt.
- Tab-Leiste der Indexserver-Konsole eingerückt, damit sie nicht mehr mit der
  abgerundeten linken Fensterkante überlappt.
- Qt-Absturz beim Schließen der eigenständigen Indexserver-Konsole behoben:
  Das Fenster wird ins Tray ausgeblendet und laufende Serverabfragen werden beim
  echten Beenden kontrolliert abgeschlossen.
- Server-Badge mit grünem Onlinepunkt, rotem Offlinepunkt, orangem Problemstatus
  und animiertem grünem Halbkreis während eines Indexlaufs ergänzt.
- Automatisches Laufintervall in Zahlenfeld und Minuten-/Stundenauswahl getrennt,
  auf 15 Minuten bis 48 Stunden begrenzt und serverseitig dauerhaft gespeichert;
  Live-Aktualisierungen überschreiben ungespeicherte Eingaben nicht mehr.

- Docker-Indexer zur alleinigen Indexquelle erweitert.
- Unveränderliche, SHA-256-geprüfte Generationen mit drei Serverbackups ergänzt.
- Automatischen Clientdownload, atomare Aktivierung, drei lokale Backups und
  Offline-Fallback ergänzt.
- Authentifizierte Kunden-API mit Revisionen, HTTP-409-Konflikten und dauerhafter
  Offline-Queue ergänzt.
- Coverage-Gate für die nun zusätzlich real getesteten HTTP-/Prozessadapter auf
  weiterhin projektweite 93 Prozent kalibriert; Adapter bleiben in der Messung.

## 0.4.0

- Headless-Docker-Dienst für Katalog, Kundenerkennung und Inhaltsindexierung.
- Compose-Beispiel mit read-only Quelldaten und persistenten Daten-/Config-Mounts.
- Isolierung der MainWindow-Workflow-Tests; der blockierende Indexworkflow-Test
  startet keine echten Controller oder Worker mehr.
- Migration des PDF-Fallbacks von deprecated `PyPDF2` auf `pypdf`.
- Aktuelle `QMouseEvent`-Signaturen in den Widgettests.
- Portable QSettings-Speicherung gilt nun für Ini- und native Qt-Formate, damit
  der Docker-Config-Mount zuverlässig verwendet wird.
- Bereinigte Coverage-Warnungen für absichtlich nicht importierte Module in
  isolierten Testprozessen.

## 0.3.0

- Aufteilung des Indexes in schnellen Katalog und fortsetzbare Inhalts-Shards.
- Erweiterte Kundenverwaltung, Kundenerkennung, Viewer und Indexdiagnose.
