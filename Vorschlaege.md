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

## Überarching (Meine Analyse)

Diese Punkte sind das Ergebnis einer vollständigen Code-Durchsicht aller Module und fokussieren auf konkrete architektonische Schwachstellen, die im laufenden Betrieb auffallen werden. Sie überschneiden sich nicht mit den bisherigen Vorschlägen.

### 1. Index-Job-Zustand: Kein Timeout-Mechanismus

`index_job.json` ist ein State-Machine-Dateisystem (`starting → running → ready/completed/cancelled/error`). Aktuell gibt es **keine Zeitbegrenzung** für langsame Läufe — z. B. bei einem Netzwerk-Einbruch kann `running` wochenlang stehen und der Nutzer weiß nicht, ob noch gearbeitet wird oder ob hängen geblieben ist.

- [ ] **Heartbeat/Last-Updated-Markierung**: In `index_job.json` ein Feld `"last_activity"` (ISO-Timestamp) pflegen, das bei jeder Fortschrittsänderung aktualisiert wird. Die GUI zeigt "Letzte Aktivität: vor 3 Stunden" und markiert hängende Läufe visuell.
- [ ] **Maximale Laufzeit konfigurierbar**: `IndexOptions` bekommt ein Feld `max_run_duration: Optional[timedelta]`. Wird die Grenze überschritten, wird der Job auf `"error"` mit Message `"timeout_exceeded"` gesetzt — kein manuelles Abbrechen nötig.

### 2. FolderStructureClassifier: Muster sind hartkodiert und nicht erweiterbar

In `folder_structure.py` ist die Erkennung von Kundendateien über fest programmierte Regex-Muster (`DIREKT_LEISTUNG`, `JAHR_NACHNAME_ORT`, etc.) implementiert. Das System funktioniert für "Dienstleistung/Jahr/Nachname, Ort" — aber jede neue Ordnerstruktur erfordert einen Code-Change + Releas.

- [ ] **Konfigurierbare Klassifikationsregeln**: Eine JSON/YAML-Datei (z. B. `classifier_rules.json`) definieren, in der Regeln wie `{ "pattern": "Dienstleistung/{jahr}/{name}, {ort}", "service_type": "Baubegleitung" }` gespeichert werden. Der Classifier liest diese beim Start und ist damit ohne Code-Änderung erweiterbar.
- [ ] **Fallback-Kategorie "Unbekannt" mit manueller Zuordnung**: Nicht klassifizierte Ordner landen in einer Pufferliste, die der Nutzer per Drag & Drop oder Dialog zuordnen kann (ähnlich wie die bestehende `customer_recognition_review`).

### 3. CustomerSuggestion: Regex-Suite ist fragil bei internationalen Namen/Adressen

`customer_suggestion.py` nutzt feste Regex-Muster (`EMAIL_RE`, `PHONE_RE`, `POSTAL_CITY_RE`, `STREET_RE`, `NAME_HINT_RE`, `SALUTATION_RE`). Diese sind auf deutsche Formate optimiert und brechen bei:
- Internationalen Kunden (chinesische/eastern-european Namen)
- Mobilnummern mit Landesvorwahl (+49, +33, etc.)
- Adressen ohne Postleitzahl am Anfang

- [ ] **Erweiterbare Matcher-Schnittstelle**: Statt harter Regex-Konstanten eine kleine Plugin-API (`IFieldExtractor`) einführen. Standardmäßig wird der deutsche Extractor geladen, aber via `settings.json` kann ein internationaler Extractor aktiviert werden.
- [ ] **Konfidenz-Scoring mit Schwellwert**: Aktuell werden alle Erkennungen als Vorschlag angenommen. Ein minimum confidence threshold (z. B. 0.6) würde False-Positives reduzieren — der Nutzer sieht nur noch plausible Vorschläge.

### 4. FileSystemMonitor: Polling-basiert, aber ohne Backoff

Der `FileSystemMonitor` pollt alle ~12 Sekunden permanent — auch nachts oder am Wochenende, wenn sich nichts ändert. Das verbraucht CPU und erzeugt unnötige Datei-IO auf der NAS.

- [ ] **Exponential Backoff bei inaktiv**: Wenn über mehrere Polling-Zyklen kein Change erkannt wird, die Intervalle verdoppeln (12s → 24s → 48s → max 5 Min) und bei Erkennung wieder auf 12s zurücksetzen.
- [ ] **Alternative: OS-native File Events**: Unter Windows `ReadDirectoryChangesW` (`win32file.FindNextChangeNotification`) statt Polling nutzen, wo verfügbar. Das eliminiert CPU-Last komplett — der Monitor reagiert nur bei echten Änderungen.

### 5. DocumentConverter: Fehlerbehandlung ist inkonsistent

In `document_converter.py` gibt es mehrere Fehlschlag-Szenarien (LibreOffice nicht installiert, catdoc fehlschlägt, python-docx crashed), die unterschiedlich behandelt werden — manche werfen Exceptions, andere loggen und springen zurück. Das führt dazu, dass bei einem Teilverlust der Konvertierung unklar ist, welche Dateien fehlgeschlagen sind.

- [ ] **Zentrales Conversion-Error-Tracking**: Alle fehlgeschlagenen Konvertierungen in einer separaten Tabelle `conversion_errors` (datei_path, fehler_typ, nachricht, datum) protokollieren und im Index-Dashboard anzeigen ("3 Dateien konnten nicht konvertiert werden").
- [ ] **Retry-Mechanismus mit Exponential Backoff**: Einmal fehlgeschlagene Konvertierungen automatisch nach 5/15/30 Minuten erneut versuchen — besonders hilfreich bei temporären LibreOffice-Sperrkonflikten.

### 6. Suchergebnisse: Keine Persistenz von "Favoriten" oder "Kürzlich"

Die aktuelle Suche ist zustandslos — jeder Suchbegriff wird sofort abgearbeitet und verworfen. Es gibt keine Möglichkeit, häufig genutzte Suchen zu speichern oder kürzliche Ergebnisse schnell wiederzufinden.

- [ ] **Suchhistorie lokal persistieren**: Die letzten 20 Suchbegriffe in `QSettings` (oder einer kleinen SQLite-Tabelle) ablegen mit Zeitstempel. Ein Dropdown neben dem Suchfeld zeigt "Kürzlich gesucht" an.
- [ ] **Favoriten-Sterne für Kunden/Dateien**: In der CustomerPage und FolderPage einen Stern-Button, der Einträge in eine `favorites`-Tabelle schreibt — analog zu vielen Dateimanagern (Windows Explorer, Finder).

### 7. GUI: Keine visuellen Indikatoren für Index-Fortschritt

Während ein Indexierungslauf läuft (`index_job.json` state = `"running"`), gibt es **keine visuelle Rückmeldung** im UI — der Nutzer sieht nicht, wie weit der Prozess fortgeschritten ist oder welche Dateien gerade verarbeitet werden.

- [ ] **Progress-Balken in der Statusleiste**: Ein `QProgressBar` oder animierter "Indexiere..."-Text mit aktuellem Fortschritt (z. B. "4.200 / 12.500 Dateien") in der Haupt-Navigation anzeigen, solange der Job läuft.
- [ ] **Live-Log-Ausgabe**: Ein kleines Log-Fenster zeigt die aktuell verarbeiteten Pfade — besonders hilfreich bei großen Datenmengen und zur Fehlersuche.

### 8. CustomerPage: Keine Möglichkeit, Kontakte zu mergen oder Duplikate aufzulösen

Die Kundenerkennung kann denselben Kunden mehrmals erkennen (z. B. "Müller, Berlin" und "Müller GmbH, Berlin") und erzeugt separate Einträge. Es gibt keinen Merge-Workflow.

- [ ] **Duplikatserkennung mit Merge-Vorschlag**: Beim Speichern eines neuen Kunden prüfen, ob ein ähnlicher Name/Ort existiert (ähnlich wie `fuzzy_search._token_similarity`). Falls ja: Dialog "Kunde 'Müller' existiert bereits. Zusammenführen?" anzeigen.
- [ ] **Merge-Funktion in CustomerPage**: Zwei Kundeneinträge zusammenführen — Felder des neueren Eintrags übernehmen, alte Einträge löschen, alle zugehörigen Dateien neu verknüpfen.

### 9. Theme: Kontrast-Slider ist fein granular, aber ohne Vorschau

Der `contrast_slider` (70-140) verändert den Kontrast der Farben live — aber der Nutzer sieht erst das Ergebnis nach dem Schließen des Settings-Popups (weil die Anwendung restartet wird). Das macht Feintuning mühsam.

- [ ] **Live-Vorschau im Settings-Dialoog**: Den Kontrast in Echtzeit auf einem Preview-Bereich anwenden (z. B. ein Miniatur-Fenster mit typischer Inhaltsansicht), bevor die Einstellungen gespeichert werden.
- [ ] **Kontrast-Presets**: "Standard", "Hoher Kontrast" (für ältere Augen), "Augenschonend" als vorkonfigurierte Werte anbieten, statt manueller Slider-Kalibrierung.

### 10. Architektur: Fehlende Integrationstests zwischen Core und GUI

Der Code ist gut in Core/GUI/Services getrennt, aber es gibt **keine Tests**, die die Grenzen zwischen diesen Schichten testen — z. B. ob `IndexManager` korrekt Daten für die `SearchPage` bereitstellt oder ob `CustomerRepository` im Kontext der GUI-Konfiguration funktioniert.

- [ ] **Integrationstest-Suite**: Tests, die eine vollständige Pipeline simulieren: Dateisystem → FileSystemMonitor → IndexManager → FTS5-Suche → SearchPage-Datenmodell. So wird sichergestellt, dass Refactorings in einem Layer keine anderen brechen.
- [ ] **Test für `index_job.json` State-Machine**: Tests, die alle Zustandübergänge (`starting → running → ready/error/cancelled`) durchspielen und sicherstellen, dass der GUI-State korrekt reagiert (z. B. "Index läuft" wird angezeigt, wenn Job in `running`).

