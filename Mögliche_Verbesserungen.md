# Mögliche Verbesserungen als Checkliste

## 1. Offene Punkte aus den Vorschlägen

- [ ] E-Mails aus einem konfigurierten IMAP-Postfach abrufen und Anhänge automatisch in die Ordnerstruktur einordnen
- [ ] IMAP-Verbindung (Server, Port, TLS, Anmeldedaten) im Einstellungs-Dialog konfigurierbar machen
- [ ] E-Mails und Anhänge wie reguläre Dokumente indizieren und durchsuchbar machen
- [ ] Anmeldedaten nicht im Klartext in QSettings ablegen, stattdessen keyring nutzen
- [ ] Eingehende E-Mails via Kundenerkennung automatisch zuordnen
- [ ] Kontextmenüs: Rechtsklick auf Ergebnis-Zeilen (ResultRow), Datei-Einträge im FolderPage-Baum und Kunden-Einträge mit passenden Aktionen
- [ ] Maus-Hover konsistent: Hover-Highlighting auch für Datei-Einträge in FolderPage und Kontaktzeilen in CustomerPage
- [ ] Installer/Deployment: Windows Installer (Inno Setup oder NSIS) mit eingebetteter Venv und Abhängigkeiten
- [ ] Installer/Deployment: macOS .app-Bundle (PyInstaller oder py2app), optional als .dmg
- [ ] Installer/Deployment: Linux AppImage oder Flatpak
- [ ] Auto-Updates: GitHub Releases per API prüfen und Nutzer über neue Versionen informieren
- [ ] Auto-Updates: optional automatischer Download und Neustart

## 2. Fehlende oder unvollständige Features

### Startup-Skripte für Windows und macOS
- [x] Windows: start.bat oder start.ps1 analog anlegen (Venv aktivieren, python main.py starten)
- [x] macOS: start.command oder Shell-Skript mit .venv/bin/activate

### Ripgrep-Unterstützung
- [ ] RIPGREP_AVAILABLE in config.py ist aktuell als Platzhalter zu prüfen (entweder vollständig nutzen oder entfernen)

### Stille Trunkierung im Tabellenviewer
- [ ] Hinweistext einblenden: Anzeige ist auf 500 Zeilen/50 Spalten begrenzt

### Onboarding / erster Start
- [ ] Einrichtungs-Dialog beim ersten Start, wenn kein gültiger Datenpfad konfiguriert ist

### Leerer app/gui/panels/-Ordner
- [ ] Ordner inhaltlich nutzen oder bereinigen

## 3. Technische Verbesserungen und Code-Qualität

### Logging
- [ ] StreamHandler für Konsole im Entwicklungs-/Debug-Betrieb ergänzen (z. B. über PAPAGUI_DEBUG=1)

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

## 4. Cross-Platform-Verbesserungen

- [x] Start-Skript: start.bat / start.ps1 für Windows ergänzen
- [x] Tray-Icon: optionales System-Tray-Icon für Status bei minimierter App
- [x] Datei extern öffnen: Fehlerfälle sichtbar dem Nutzer melden
- [x] HiDPI / Retina: QApplication.setHighDpiScaleFactorRoundingPolicy explizit setzen
- [ ] Pfad-Trennzeichen: verbliebene String-Pfade konsequent auf pathlib.Path umstellen
- [ ] Prozess-Signale: optional os.kill(..., signal.CTRL_BREAK_EVENT) unter Windows prüfen
- [ ] Schriftarten: Mindestgröße und Lesbarkeit auf Windows/macOS/Linux gezielt testen

## 5. Tests und Qualitätssicherung

### Integrationstests / Smoke-Tests
- [x] MainWindow Smoke-Test
- [x] CustomerRecognitionReviewDialog Smoke-Test
- [x] SearchWorker Test
- [x] SettingsPopup Smoke-Test
- [ ] IndexJobController dedizierten GUI-nahen Testfall für Controller-Lebenszyklus weiter ausbauen

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
- [ ] Barrierefreiheit: accessibleName()/setAccessibleDescription() für zentrale Widgets setzen
- [ ] Sortierung der Suchergebnisse konfigurierbar machen (Relevanz/Datum/Alphabet)
- [ ] Vorschau im Suchergebnis mit Snippet (FTS5 snippet()) anzeigen
- [ ] Drag & Drop: Dateien aus Dateibaum in externe Apps ziehen
- [ ] Drag & Drop: Dokumente per Drop in Ordner importieren

## 7. Sonstige Hinweise

- [x] Dokumentation: README Architekturabschnitt aktualisieren (inkl. panels)
- [x] Versionsnummer: __version__ und pyproject.toml ergänzen
- [x] requirements-lock.txt mit exakten Versionen ergänzen
