# Lokaler Windows-Teststand vom 11. September 2026

> Historischer Stand. Die damaligen vier ACL-Fehler wurden am 14. September als
> Sandbox-Effekt bestätigt: außerhalb der Sandbox bestehen alle 17 Tests des
> betroffenen Moduls. Aktuelle Releaseprüfungen: [Releasevorbereitung](../releasing.md).

Ausgangscommit: `e3193d3`, zusätzlich die lokale Integration von Graphify,
pytest-qt und Pyinstrument. Windows, Python 3.14.2 in `.venv`, PySide6 6.11.1
aus dem Client-Lock. Die CI verwendet weiterhin Python 3.11; dieser lokale Lauf
ist keine Bestätigung der CI oder der Releasefähigkeit von Version 0.5.

## Nachkorrektur

Die unten dokumentierten ursprünglichen Fehler wurden anschließend untersucht:

- Native Pfade und Dateiendungen in Testdaten ergänzt; Plattformsimulationen ändern
  nicht mehr das globale `os.name` und damit unbeabsichtigt `pathlib`.
- Shell-Prozessfixtures durch portable Python-Prozesse ersetzt und betriebsspezifische
  Zeilenenden berücksichtigt. Git Bash prüft jedes POSIX-Startskript einzeln.
- Simulierte Linux-Ressourcen funktionieren unabhängig vom ausführenden Betriebssystem.
  Symlinktests ohne das erforderliche Windows-Recht melden einen ausdrücklichen Skip.
- Office-/PDF-Worker schreiben codepage-unabhängiges JSON; Unicode bleibt beim Lesen erhalten.
- Die GUI verwendet das konfigurierte QSettings-Format. Dadurch verwenden auch Theme,
  Sucheinstellungen und Ordneransicht im Test tatsächlich den isolierten INI-Speicher.
- Die Neuaufbau-Schaltfläche passt auch in das kleine Verwaltungsfenster.
- Die PDF-Testfixture nutzt die bereits deklarierte ReportLab-Abhängigkeit.
- Der überprüfte alte `app/`-Baum enthielt ausschließlich Bytecode und wurde entfernt.
- Coverage-Zwischenstände entstehen in einem temporären Verzeichnis und werden auch bei
  Testfehlern und Abbruch zusammengeführt. Die bisherigen Einzeldateien sind bereinigt.
  Neue Regressionen prüfen Testfehler, Strg+C und Fehler beim Zusammenführen.

Der abschließende isolierte Gesamtlauf über alle 81 Module ergab 981 bestandene
Tests, fünf begründete Überspringungen und vier fehlgeschlagene Tests.
Die gesammelte Branch-Coverage beträgt 94,77 Prozent; wegen der Testfehler ist
dies trotzdem kein grüner Gesamtlauf. Es blieben keine `.coverage.*`-Zwischenstände
im Projektverzeichnis zurück. Ruff und die Dependency-Prüfung bestanden.

Der isolierte Gesamtlauf meldete nach den Korrekturen nur noch Fehler in
`tests.client.test_client_settings`: vier Zugriffsfehler nach der Windows-ACL-Härtung.
Dasselbe unveränderte Konfigurationsmodul bestand außerhalb der Sandbox vollständig
mit 17 Tests. Seine Sicherheitslogik wurde deshalb nicht abgeschwächt.

Ein vollständiger Lauf außerhalb der Sandbox wurde von der automatischen Freigabeprüfung
wegen eines erreichten Nutzungslimits abgelehnt. Die native Gesamtprüfung bleibt daher
als gesonderter Nachweis offen:

```powershell
.\.venv\Scripts\python.exe tests/run_ci.py --coverage
```

Der folgende ursprüngliche Befund bleibt zur Nachvollziehbarkeit erhalten.

## Bestätigte Werkzeugfunktionen

- Graphify 0.9.53 mit Gemini-Unterstützung und installiertem Codex-Skill.
- Synthetische Gemini-Anfrage mit dem Graphify-Standardmodell
  `gemini-3-flash-preview` erfolgreich. Der Key liegt außerhalb des Repositorys
  als benutzergebundener Windows-DPAPI-Blob; keine Schlüsselwerte im Bericht.
- Lokaler AST-Graph: 165 Paketquelldateien, 2.582 Knoten, 6.729 Kanten.
- Lokale Graphabfrage nach `SourcePathResolver` erfolgreich.
- Zehn Layoutregressionen mit pytest-qt erfolgreich.
- Pyinstrument-Bericht aus drei Durchläufen des synthetischen
  Kundenerkennungsbenchmarks unter `profiles/recognition.html` erzeugt.
- Ruff und `pip check` erfolgreich.

## Gesamtprüfung

`python tests/run_ci.py --coverage` wurde mit temporären Testdaten ausgeführt.
18 Testmodule meldeten Fehler. Wegen der Testfehler beendet der Treiber den Lauf
vor dem Zusammenführen und Bewerten der Coverage; das 93-Prozent-Gate ist damit
nicht bestätigt.

Fehlerhafte Module:

```text
tests.architecture.test_package_boundaries
tests.client.test_client_settings
tests.client.test_conversion_edges
tests.client.test_generation_store_edges
tests.client.test_global_catalog
tests.client.test_http_config_entrypoints
tests.client.test_paths_and_catalog
tests.client.test_recognition_admin
tests.client.test_restored_shell_integration_coverage
tests.client.test_viewers_gui
tests.client.test_viewers_services
tests.server.test_document_extraction_structured
tests.server.test_extraction_command_concurrency
tests.server.test_recognition_engine_migration_preview
tests.server.test_worker_resources
tests.system.test_wire_interop
tests.tools.test_run_ci
tests.tools.test_start_scripts
```

## Ansatzpunkte für das nächste Bugfixing

- Der lokale alte `app/`-Baum existiert noch; ein Architekturtest verlangt seine
  vollständige Abwesenheit. Vor einer Bereinigung die verbliebenen Dateien prüfen.
- Mehrere Tests erwarten POSIX-Dateirechte, Linux-Pfade, `resource` oder
  `os.sched_getaffinity`; außerdem unterscheiden sich Prozesszeilenenden unter Windows.
- Ein Symlinktest setzt ein Windows-Recht voraus, das im Testprozess fehlt.
- Konfigurationsprüfungen melden Zugriff verweigert bei atomarem Ersetzen nach
  ACL-Härtung. Sandbox-Effekte und tatsächliches Verhalten getrennt nachprüfen.
- Der QSettings-Isolationstest erhält einen Registry-Pfad trotz gesetztem
  INI-Standardformat. Ursache im Windows-/Qt-Verhalten prüfen.
- Der POSIX-Syntaxcheck findet den WSL-Bash-Starter, dessen Ausführung hier
  mit Zugriff verweigert scheitert.

Die Fehler zu POSIX-Dateirechten, dem verbliebenen `app/`-Baum und QSettings
wurden zusätzlich mit deaktiviertem Plugin (`-p no:pytest-qt`) reproduziert.
Für die übrigen Fehler ist die Ursache noch nicht abschließend untersucht.
