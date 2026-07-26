## Papa

- integration vom Imap Server
  - Ordner View mit Tabs ergänzen um zwischen ordner & Mails hin und her zu wechseln
 

## Nici

- NAS Server als Haubt-Daten Quelle verwendbar machen.
- verinfachen der Bedienung
  - angepasstem Kontext Menü bei allen möglichen Dingen?
  - Maus Hoover konsequent machen 
- Installer für das Deplyment
- Auto Updates wenn neue Versionen raus kommen? 
- Kundenerkennung vor 2016 auch bei den Prüffällen ausschöießenAusschließen
- unterordner unter Dienstleistung/Jahr/Name, Ort/`Unterordner` von der globalen suche Standartgemäß ausschließen aber via Filteroption hinzufügen.
- Auf der Startseite beim neu öffnen der App unten gesammtanzahl der Kunden & Ordner anzeigen
- in den Einstellungen Statistiken Tab mit Statistiken ergänzen 


## Patrick

---

## Allgemeine Verbesserungsvorschläge (KI-Analyse (ornith:35b))

### A. Architektur & Code-Qualität

- [ ] **Type-Hints überall konsistent**: `index_manager.py` nutzt noch teilweise implizite Typen (z. B. `self._folder_classifier`, `self._document_converter`). Pylance `source.addTypeAnnotation` könnte die restlichen Methoden und Attribute nachziehen.
- [ ] **`IndexManager.APP_VERSION` entfernen**: Die Versionsnummer ist hardcoded in `app/core/index_manager.py` (`"0.2"`), obwohl `pyproject.toml` `"0.3.0"` enthält. Besser: `from app import __version__` oder aus `pyproject.toml` lesen, damit beide Quellen konsistent bleiben.
- [ ] **`__init__.py`-Exporte überprüfen**: In `app/gui/__init__.py`, `app/core/__init__.py` und den Subpackages prüfen, ob alle öffentlichen Symbole exportiert werden (aktuell sind einige Imports nur über Deep-Pfade erreichbar). Das erleichtert Unit-Tests und verringert die Import-Tiefe.
- [ ] **Magic Strings/Numbers reduzieren**: `INDEX_BATCH_SIZE = 100`, `500 Zeilen / 50 Spalten` für die Trunkierung, `BACKUP_COUNT = 3` — alles Kandidaten für konfigurierte Konstanten (z. B. über `IndexOptions`).

### B. Performance & Skalierbarkeit

- [ ] **FTS5-Volltextindex auf `files`-Tabelle erweitern**: Aktuell wird nur in `file_content_fts` gesucht. Ein FTS5-Trigger (`CREATE VIRTUAL TABLE ... USING fts5(...)`) direkt auf der `files`-Tabelle würde auch Spalten wie `customer_name`, `project_name`, `service_type` durchsuchbar machen, ohne dass man den Content extrahieren muss.
- [ ] **Index-Datei-Größen-Budget**: Bei wachsendem Datenbestand (>50k Dateien) wird der vollständige Snapshot im `FileSystemMonitor.build_snapshot()` langsam. Eine inkrementelle Berechnung (nur geänderte Pfade vergleichen, nicht die gesamte Liste neu aufbauen) würde das polling deutlich schneller machen.
- [ ] **`ThreadPoolExecutor`-Pool wiederverwenden**: Im `IndexManager` wird bei jedem Index-Lauf ein neuer Pool erstellt. Ein class-weiter Singleton-Pool mit `max_workers`-Limitierung spart Thread-Erstellungs-Overhead und verhindert Ressourcen-Lecks.
- [ ] **SQLite-WAL-Modus auch für die Haupt-Datenbank**: `CustomerRepository` nutzt WAL (`PRAGMA journal_mode = WAL`), aber der Index (`IndexManager`) nicht. Für schreibintensive Läufe (Reindexierung) lohnt sich WAL auch hier, um Lesesperren zu vermeiden.

### C. Sicherheit & Datenschutz

- [ ] **IMAP-Anmeldedaten in Keyring speichern**: Die Planung erwähnt das bereits. Parallel: `QSettings` ist kein sicherer Speicher für sensible Daten — selbst auf dem lokalen Rechner sind INI/Registry-Einträge lesbar. Empfehlung: Windows Credential Manager (`keyring`-Package), macOS Keychain, Linux Secret Service API.
- [ ] **Sensible Daten in Logs maskieren**: Prüfen, ob E-Mail-Adressen, Telefonnummern oder Kundennamen unbeabsichtigt im Debug-Log landen (z. B. bei `RecognitionCandidate.to_dict()`). Ein einfacher `mask_email()`, `mask_phone()` in `customer_models.py` wäre sinnvoll.
- [ ] **SQLite-Verschlüsselung für `customers.db`**: Bei sensiblen Kontaktdaten (E-Mail, Telefon, Straße) wäre SQLCipher als Drop-in-Ersatz für `sqlite3` eine Überlegung wert — besonders wenn die NAS-Freigabe auch von anderen Lesern erreichbar ist.

### D. UX & Bedienerfreundlichkeit

- [ ] **Drag & Drop für Ordner-Auswahl**: Statt nur den Onboarding-Dialoog oder einen Dateidialog zu nutzen, könnte man per Drag & Drop einen Datenordner direkt auf das App-Fenster ziehen (`dragEnterEvent` / `dropEvent`). Das ist die intuitive Methode, die NAS-Nutzer erwarten.
- [ ] **Suchfeld mit Auto-Vervollständigung**: Die aktuelle Suche wartet bis Enter gedrückt wird. Eine Live-Autovervollständigung (ähnlich wie in der Adressverwaltung) würde das Tippen beschleunigen — besonders bei langen Kundennamen.
- [ ] **Breadcrumbs / Pfad-Brotkrümelnavigation**: In `FolderPage` und `CustomerPage` fehlen Breadcrumbs. Aktuell muss man über "Zurück" navigieren; ein Breadcrumb-Bar (z. B. `Datenquelle > Dienstleistung > 2024 > Müller, Berlin`) wäre ergonomisch besser.
- [ ] **Tastatur-Kürzel für häufige Aktionen**: Strg+F (Suchfeld fokussieren), Strg+N (neuer Kunde), Escape (Zurück/Abbrechen) — die fehlende Tastaturnavigation ist besonders bei Maus-Hover-Pfaden ein Problem.

### E. NAS / Remote-Storage

- [ ] **SMB/CIFS-Mount-Prüfung beim Start**: Wenn der Datenpfad auf einem NAS liegt, sollte geprüft werden, ob der Mount noch aktiv ist (z. B. `Path(root).exists()` + Ping), und im Fehlerfall eine klare Meldung mit Reconnect-Option anzeigen.
- [ ] **Netzwerk-Timeouts konfigurierbar machen**: Für langsame WLAN/VPN-Verbindungen sind die aktuellen Timeouts (`timeout=30` in `DocumentConverter._run_command`) zu kurz. Ein Setting für Netzwerk-Timeouts (z. B. 60–120 Sekunden) wäre hilfreich.
- [ ] **Offline-Cache für近期 geöffneter Dateien**: Bei NAS-Nutzung ohne ständige Verbindung:近期 geöffnete Dateien lokal zwischenspeichern und im Viewer anzeigen, auch wenn die Quelle nicht erreichbar ist.

### F. Testing & CI

- [ ] **Testabdeckung der GUI erhöhen**: Viele Tests sind Smoke-Tests (`test_gui_smoke.py`). Unit-Tests für `fuzzy_search._token_similarity`, `folder_structure.FolderStructureClassifier.classify` und `customer_recognition.RecognitionBlacklist.filter_suggestion` würden die Stabilität bei Refactorings deutlich verbessern.
- [ ] **Pytest-Fixture für temporäre SQLite-Datenbanken**: Viele Tests erstellen manuell `Path(tempfile.mkdtemp()) / "index.db"`. Eine shared Fixture (`@pytest.fixture def fake_index_db(tmp_path)`) würde das DRY-Prinzip durchsetzen und die Testlaufzeit verkürzen.
- [ ] **Test für `FileSystemMonitor.compare_snapshots` mit Edge Cases**: Leere Snapshots, identische Snapshots, nur neue Dateien — diese Fälle sollten explizit getestet werden, da sie direkt die Reindexierungslogik beeinflussen.

### G. Dokumentation & Onboarding

- [ ] **Developer Setup Guide**: Die README erwähnt das Start-Skript, aber nicht, wie man Unit-Tests ausführt (`pytest tests/`), wie der Index neu aufgebaut wird oder wo Logs zu finden sind. Ein Abschnitt "Für Entwickler" wäre hilfreich.
- [ ] **Architektur-Dokumentation (ADR)**: Für Entscheidungen wie "WAL statt SQLite-Standard", "FTS5 statt Elasticsearch" oder "QSettings statt PostgreSQL" könnten Architecture Decision Records (ADRs) als Markdown-Dateien im Repo liegen und die Nachvollziehbarkeit erhöhen.

### H. Zukünftige Features / Roadmap-Ideen

- [ ] **IMAP-E-Mail-Anbindung** (bereits in der Planung): E-Mails indizieren, Anhänge wie Dokumente behandeln, eingehende Mails per Kundenerkennung zuordnen.
- [ ] **Export-Funktion**: Kundenliste als CSV/Excel exportieren, Suchergebnisse als PDF oder Markdown. Nützlich für Berichte und Backups.
- [ ] **Multi-Index-Support**: Neben dem Hauptindex (NAS) einen zweiten lokalen Index für schnelle Suche auf dem Entwicklungsrechner ermöglichen — mit synchronisierbarem FTS5-Subset.
- [ ] **Plugin-Architektur für Dokument-Konverter**: Der `DocumentConverter` ist aktuell monolithisch (LibreOffice/catdoc). Eine kleine Plugin-Schnittstelle (`IDocumentExtractor`) würde den Austausch oder die Erweiterung von Konvertern erleichtern, ohne den Core zu ändern.

