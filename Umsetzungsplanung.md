# Mögliche Verbesserungen als Checkliste

## 1. Offene Punkte aus den Vorschlägen

- [ ] E-Mails aus einem konfigurierten IMAP-Postfach abrufen und Anhänge automatisch in die Ordnerstruktur einordnen
- [x] Mail-Ansicht ergänzen: In der Ordneransicht Tabs für "Ordner" und "Mails" bereitstellen
- [ ] IMAP-Verbindung (Server, Port, TLS, Anmeldedaten) im Einstellungs-Dialog konfigurierbar machen
- [ ] E-Mails und Anhänge wie reguläre Dokumente indizieren und durchsuchbar machen
- [ ] Anmeldedaten nicht im Klartext in QSettings ablegen, stattdessen keyring nutzen
- [ ] Eingehende E-Mails via Kundenerkennung automatisch zuordnen (Vorbereitung, nicht final)
- [x] Kontextmenüs: Rechtsklick auf Ergebnis-Zeilen (ResultRow), Datei-Einträge im FolderPage-Baum und Kunden-Einträge mit passenden Aktionen
- [x] Maus-Hover konsistent: Hover-Highlighting auch für Datei-Einträge in FolderPage und Kontaktzeilen in CustomerPage
- [ ] Installer/Deployment: Windows Installer (Inno Setup oder NSIS) mit eingebetteter Venv und Abhängigkeiten
- [ ] Installer/Deployment: macOS .app-Bundle (PyInstaller oder py2app), optional als .dmg
- [ ] Installer/Deployment: Linux AppImage oder Flatpak
- [x] Headless-Docker-Prototyp für Katalog, Kundenerkennung und Inhaltsindexierung
- [ ] Unveränderliche Indexgenerationen mit Manifest/Prüfsummen für Client-Sync
- [ ] Atomarer Client-Download und Aktivierung zentral erzeugter Indexgenerationen
- [ ] Auto-Updates: GitHub Releases per API prüfen und Nutzer über neue Versionen informieren
- [ ] Auto-Updates: optional automatischer Download und Neustart

## 2. Fehlende oder unvollständige Features

### Startup-Skripte für Windows und macOS
- [x] Windows: start.bat oder start.ps1 analog anlegen (Venv aktivieren, python main.py starten)
- [x] macOS: start.command oder Shell-Skript mit .venv/bin/activate

### Ripgrep-Unterstützung
- [x] RIPGREP_AVAILABLE in config.py ist aktuell als Platzhalter zu prüfen (entweder vollständig nutzen oder entfernen)

### Stille Trunkierung im Tabellenviewer
- [x] Hinweistext einblenden: Anzeige ist auf 500 Zeilen/50 Spalten begrenzt

### Onboarding / erster Start
- [x] Einrichtungs-Dialog beim ersten Start, wenn kein gültiger Datenpfad konfiguriert ist
- [x] NAS-Server als primäre Datenquelle sauber unterstützen (Pfadwahl, Erreichbarkeit, Reconnect)

### Leerer app/gui/panels/-Ordner
- [x] Ordner inhaltlich nutzen oder bereinigen

## 3. Technische Verbesserungen und Code-Qualität

### Logging
- [ ] StreamHandler für Konsole im Entwicklungs-/Debug-Betrieb ergänzen (z. B. über PAPAGUI_DEBUG=1)

### Security
- [x] Security-Konzept dokumentieren und Mindestmaßnahmen ableiten (Secrets, Rechte, Dateizugriff, Update-Vertrauen)

### SETTINGS_ORG / SETTINGS_APP
- [x] Doppelte Definition entfernen, theme.py importiert Konstanten aus app.core.config

### Context-Manager für IndexManager
- [x] __enter__/__exit__ implementieren und Verbindungen zuverlässig schließen

### SQL-Injection-Risiko in _ensure_column
- [x] Tabellen- und Spaltennamen validieren und sicher quoten

### index_directory()-Wrapper
- [x] Wrapper offiziell als veraltet markieren (DeprecationWarning)

### Konfigurierbares Arbeitsverzeichnis
- [x] Pfadauflösung robust machen und nicht von os.getcwd() abhängig

### Kundenerstellung
- [x] Fehlerfall bei Kundenerstellung/-zuordnung ("Kunden-Blending") reproduzieren, Ursache dokumentieren und in Task unterteilen

## 4. Cross-Platform-Verbesserungen

- [x] Start-Skript: start.bat / start.ps1 für Windows ergänzen
- [x] Tray-Icon: optionales System-Tray-Icon für Status bei minimierter App
- [x] Datei extern öffnen: Fehlerfälle sichtbar dem Nutzer melden
- [x] HiDPI / Retina: QApplication.setHighDpiScaleFactorRoundingPolicy explizit setzen
- [X] Pfad-Trennzeichen: verbliebene String-Pfade konsequent auf pathlib.Path umstellen
- [x] Prozess-Signale: optional os.kill(..., signal.CTRL_BREAK_EVENT) unter Windows prüfen
 testen
- [x] Kundenerkennung vor 2016 explizit per Regel und Tests absichern (Verifikationspunkt)


## 5. Tests und Qualitätssicherung

### Integrationstests / Smoke-Tests
- [x] MainWindow Smoke-Test
- [x] CustomerRecognitionReviewDialog Smoke-Test
- [x] SearchWorker Test
- [x] SettingsPopup Smoke-Test
- [x] IndexJobController dedizierten GUI-nahen Testfall für Controller-Lebenszyklus weiter ausbauen
- [x] Regressionstest für QPdf-Connect-Warnung (invalid nullptr parameter) erstellen und Behebung absichern

### Kontrollfluss SearchWorker.run()
- [x] Kontrollfluss mit klarer elif-Struktur ohne schwer lesbares Fall-Through

### CI-Pipeline
- [x] GitHub Actions Pipeline vorhanden
- [x] Tests laufen auf Ubuntu + Windows + macOS
- [x] Lint-Prüfung mit ruff ergänzt

## 6. UX-Verbesserungen

- [ ] Statusmeldung während erstem Indexlauf: "Index wird erstellt, bitte warten..."
- [x] Tastatur-Navigation: Alt+Right für Vorwärts ergänzt
- [ ] Fokus-Reihenfolge (Tab-Reihenfolge), insbesondere im CustomerEditorDialog, prüfen
- [x] Barrierefreiheit: accessibleName()/setAccessibleDescription() für zentrale Widgets setzen
- [x] Sortierung der Suchergebnisse konfigurierbar machen (Relevanz/Datum/Alphabet)
- [x] Globale Suche: Unterordner unter Dienstleistung/Jahr/Name, Ort standardmäßig ausblenden und per Filter optional einblenden
- [x] Vorschau im Suchergebnis mit Snippet (FTS5 snippet()) anzeigen
- [ ] Drag & Drop: Dateien aus Dateibaum in externe Apps ziehen
- [ ] Drag & Drop: Dokumente per Drop in Ordner importieren
- [x] Startseite: beim Öffnen Gesamtanzahl von Kunden und Ordnern sichtbar anzeigen
- [x] Einstellungen: separaten Statistik-Tab mit Kernkennzahlen ergänzen

## 7. Sonstige Hinweise

- [x] Dokumentation: README Architekturabschnitt aktualisieren (inkl. panels)
- [x] Versionsnummer: __version__ und pyproject.toml ergänzen
- [x] requirements-lock.txt mit exakten Versionen ergänzen
