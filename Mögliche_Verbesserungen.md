
## 1. Offene Punkte aus den Vorschlägen

- E-Mails aus einem konfigurierten IMAP-Postfach abrufen und Anhänge automatisch in die Ordnerstruktur einordnen
- IMAP-Verbindung (Server, Port, TLS, Anmeldedaten) im Einstellungs-Dialog konfigurierbar machen
- E-Mails und Anhänge wie reguläre Dokumente indizieren und durchsuchbar machen
- Anmeldedaten **nicht** im Klartext in `QSettings` ablegen – stattdessen `keyring` (plattformübergreifend: macOS Keychain, Windows Credential Manager, Linux Secret Service) nutzen
- Überlegung: Eingehende E-Mails via Kundenerkennung automatisch zuordnen

- **Kontextmenüs**: Rechtsklick auf Ergebnis-Zeilen (`ResultRow`), Datei-Einträge im `FolderPage`-Baum und Kunden-Einträge sollte situationsgerechte Aktionen anbieten (Ordner öffnen, Datei öffnen, Datei kopieren, Kunden bearbeiten, …)
- **Maus-Hover konsequent**: `ResultRow`-Widgets haben bereits Hover-Highlighting; das fehlt noch bei den Datei-Einträgen in `FolderPage` und den Kontaktzeilen in `CustomerPage`
- **Installer / Deployment**:
  - Windows: Inno Setup oder NSIS Installer, der Python-Venv und alle Abhängigkeiten einbettet; optionales `start.bat` / `start.ps1`
  - macOS: `.app`-Bundle via PyInstaller oder `py2app`, optional als `.dmg` verteilen
  - Linux: AppImage oder Flatpak für distributions-unabhängige Verteilung
- **Auto-Updates**: Eingebauter Update-Checker, der GitHub-Releases per API abfragt und den Nutzer auf neue Versionen hinweist; optional automatischen Download + Neustart anbieten


---

## 2. Fehlende oder unvollständige Features

### Startup-Skript für Windows & macOS fehlt
- `start.sh` ist reines Bash/Linux-Skript
- **Windows**: `start.bat` oder `start.ps1` analog anlegen (Venv aktivieren, `python main.py` starten)
- **macOS**: `start.command` oder Shell-Skript, das korrekt mit `.venv/bin/activate` umgeht

### Ripgrep-Unterstützung nie fertiggestellt
- `RIPGREP_AVAILABLE = True` in `config.py` ist ein toter Platzhalter – der Flag wird nirgends ausgelesen
- Entweder den Schnell-Pfad via `rg` implementieren (besonders nützlich bei sehr großen Verzeichnissen) oder den Flag entfernen, um Verwirrung zu vermeiden

### Stille Trunkierung im Tabellenviewer
- `SpreadsheetViewerWidget` zeigt nur die ersten 500 Zeilen / 50 Spalten an, ohne den Nutzer darauf hinzuweisen
- Mindestens einen Hinweistext einblenden: „Anzeige begrenzt auf 500 Zeilen – Datei extern öffnen für vollständige Ansicht"

### Kein Onboarding / erster Start
- Beim allerersten Start ist `BAUVORHABEN_DIR` eine relative Annahme (Projektordner); gibt es dort keinen `Bauvorhaben/`-Ordner, passiert stillschweigend nichts
- Ein einfacher Einrichtungs-Dialog beim ersten Start (oder wenn kein gültiger Pfad konfiguriert ist) würde die Einstiegshürde deutlich senken

### Leerer `app/gui/panels/`-Ordner
- Namespace-Package ohne Inhalt; entweder für eine geplante geteilte Panel-Ansicht nutzen oder aufräumen

---

## 3. Technische Verbesserungen & Code-Qualität

### Logging: kein Konsolen-Handler
- Alle Log-Ausgaben landen nur in der Rotationsdatei `data/logs/papagui.log`
- Im Entwicklungs- und Debug-Betrieb fehlen Ausgaben im Terminal
- Lösung: In `logging_config.py` einen `StreamHandler` hinzufügen, der bei Debug-Level greift (z. B. via `--debug`-Flag oder Umgebungsvariable `PAPAGUI_DEBUG=1`)

### `SETTINGS_ORG`/`SETTINGS_APP` doppelt definiert
- In `config.py` **und** `theme.py` als Konstanten hinterlegt – keine Single Source of Truth
- `theme.py` sollte `from app.core.config import SETTINGS_ORG, SETTINGS_APP` importieren

### Kein Context-Manager für `IndexManager`
- `IndexManager` öffnet `self.conn` in `__init__`, erwartet aber manuelles `close()` durch den Aufrufer
- `__enter__`/`__exit__` implementieren, damit `with IndexManager(...) as mgr:` möglich wird und Verbindungen zuverlässig geschlossen werden

### SQL-Injection-Risiko in `_ensure_column`
- `ALTER TABLE {table} ADD COLUMN {column} {col_type}` wird mit f-Strings gebaut
- Alle heutigen Aufrufer nutzen Hardcoded-Strings, aber das Muster ist gefährlich
- Tabellenname und Spaltenname gegen eine Whitelist valider Bezeichner prüfen oder durch den SQLite-Identifier-Quoting-Mechanismus absichern

### `index_directory()`-Wrapper aufräumen
- `index_directory()` ist eine dünne Hülle um `synchronize_directory()` und als veraltet kommentiert, aber nicht offiziell markiert
- Mit `@deprecated` dekorieren oder komplett entfernen, um die API-Oberfläche zu vereinfachen

### Konfigurierbares Arbeitsverzeichnis
- `BAUVORHABEN_DIR = BASE_DIR / "Bauvorhaben"` ist relativ zum Skript-Startverzeichnis
- Wenn die Anwendung per Doppelklick gestartet wird (z. B. unter Windows), kann `BASE_DIR` falsch sein
- Lösung: Pfad immer relativ zur `main.py`-Datei ableiten (z. B. `Path(__file__).parent`), nicht relativ zu `os.getcwd()`

---

## 4. Cross-Platform-Verbesserungen

| Bereich | Problem | Empfehlung |
|---|---|---|
| Start-Skript | `start.sh` ist Linux/macOS-only | `start.bat` / `start.ps1` für Windows ergänzen |
| Pfad-Trennzeichen | Meistens `os.sep` verwendet, aber vereinzelt Strings mit `/` | Konsequent `pathlib.Path` verwenden |
| Prozess-Signale | `SIGTERM` wird unter Windows nicht gesendet | Bereits beachtet; `os.kill(..., signal.CTRL_BREAK_EVENT)` als Alternative für Windows prüfen |
| Tray-Icon | Nicht vorhanden | Optional: System-Tray-Icon damit der laufende Index-Job sichtbar bleibt, auch wenn das Fenster minimiert ist – funktioniert unter Qt auf allen drei Plattformen |
| Datei extern öffnen | `os.startfile` (Windows), `xdg-open` (Linux), `open` (macOS) | Bereits unterschieden im Code; sicherstellen, dass Fehler (Programm nicht gefunden) dem Nutzer gemeldet werden |
| Schriftarten | System-Schriftarten variieren | Mindestgröße und -lesbarkeit auf allen Plattformen mit Qt-Standardfonts testen |
| HiDPI / Retina | `QApplication.setHighDpiScaleFactorRoundingPolicy` | Explizit setzen, um verschwommene Icons auf Retina-Displays (macOS) und HiDPI-Monitoren (Windows) zu vermeiden |

---

## 5. Tests & Qualitätssicherung

### Fehlende Integrationstests
- `MainWindow` komplett ohne Tests
- `CustomerEditorDialog` und `CustomerRecognitionReviewDialog` ohne Tests
- `SearchWorker`, `IndexJobController`, `SettingsPopup` ohne Tests
- Ziel: Mindestens „Smoke Tests", die Widgets instanziieren und grundlegende Abläufe durchlaufen (mit `QT_QPA_PLATFORM=offscreen`)

### Kontrollfluss in `SearchWorker.run()` klären
- Die `if self.category == ...`-Kette fällt durch ohne `return`; das ist korrekt, aber schwer lesbar
- Umstrukturieren mit `elif` oder Dispatch-Dictionary, um versehentliches Fall-Through zu verhindern

### CI-Pipeline fehlt
- Keine GitHub Actions / CI-Konfiguration vorhanden
- Empfehlung: Einfache Pipeline mit `python -m unittest discover -s tests`, läuft auf Ubuntu + Windows + macOS
- Zusätzlich `flake8` oder `ruff` für Lint-Prüfungen

---

## 6. UX-Verbesserungen

### Statusmeldungen & Feedback
- Beim ersten Indexlauf gibt es keinen erklärenden Hinweis, warum die Suche noch keine Ergebnisse liefert
- Kurztext „Index wird erstellt, bitte warten…" in der Suche anzeigen, solange kein Index vorhanden ist

### Tastatur-Navigation
- `Alt+Left` für Zurück ist implementiert; `Alt+Right` für Vorwärts fehlt
- Fokus-Reihenfolge (`Tab`-Reihenfolge) in Dialogen prüfen – insbesondere `CustomerEditorDialog`

### Barrierefreiheit
- `accessibleName()` / `setAccessibleDescription()` auf zentralen Widgets setzen, damit Screen-Reader und Automatisierungstools funktionieren

### Sortierung der Suchergebnisse konfigurierbar
- Aktuell nur Relevanz-Ranking; Option zum Sortieren nach Datum (zuletzt geändert) oder alphabetisch wäre nützlich

### Vorschau im Suchergebnis
- Hover-Tooltip oder ausgeklappte Vorschau-Zeile mit dem passenden Text-Snippet (bereits im FTS5-Index als `snippet()` verfügbar) würde die Ergebnisliste informativer machen

### Drag & Drop
- Dateien aus dem Dateibaum per Drag & Drop in externe Anwendungen ziehen
- Dokumente per Drop in einen Ordner importieren (mit optionalem Kopier-Dialog)

---

## 7. Sonstige Hinweise

- **Dokumentation**: `README.md` ist gut strukturiert, aber die Architektur-Beschreibung ist leicht veraltet (z. B. fehlt `panels/` in der Auflistung). Beim nächsten Release synchronisieren.
- **Versionsnummer**: Keine `__version__` oder `pyproject.toml` vorhanden. Für Auto-Updates und Installer ist eine definierte Versionsnummer Pflicht.
- **`requirements.txt` pinnen**: Aktuell viele `>=`-Abhängigkeiten. Eine `requirements-lock.txt` mit exakten Versionen würde reproduzierbare Builds sicherstellen.
