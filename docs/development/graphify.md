# Graphify

Graphify dient hier ausschließlich als lokale Architekturkarte des Pythoncodes
in `packages/{contracts,server,client}/src/**/*.py`. Kundendokumente,
Datenbanken, Logs, produktive Konfigurationen, historische Pläne und Memories
gehören nicht zum Korpus. Der Pfad `graphify-out/` ist Git-ignoriert.

## Installation

Das Paket heißt `graphifyy`, der Befehl `graphify`. Der Versionsprüfungsworkflow
verwendet derzeit 0.9.53; die folgende Anleitung verwendet dieselbe Version:

```bash
uv tool install 'graphifyy[gemini]==0.9.53'
```

Graphify ist keine Client- oder Serverabhängigkeit. Die strukturelle
AST-Extraktion benötigt weder API-Key noch Modellanbieter. Eine semantische
Dokumentextraktion ist für dieses Projekt nicht Teil des Ablaufs.

## Codex-Skill und Gemini unter Windows

Der Codex-Skill wird mit derselben Graphify-Version ausgeliefert und liegt nach
Installation unter `~/.codex/skills/graphify/SKILL.md`, einschließlich seiner
Referenzdateien. Der Herstellerbefehl `graphify codex install` legt in Version
0.9.53 Projektregeln und einen Hook an; die Skilldateien selbst lassen sich mit
dem Paketinstaller ergänzen:

```bash
uv tool run --from 'graphifyy[gemini]==0.9.53' python -c "from graphify.install import install; install(platform='codex')"
```

Der Skill steht in Codex ab dem nächsten Turn als `$graphify` bereit.
Für PapaGUI gilt die hier dokumentierte Paketquellenauswahl auch bei Skillaufrufen.
Die lokalen Projektregeln verweisen deshalb auf den eingeschränkten Builder.

`tools/graphify.ps1` findet Graphify über `uv tool dir`, unabhängig vom aktuellen
PATH. Für `-CheckGemini` und `extract` lädt es den Gemini-Key aus
`%LOCALAPPDATA%/PapaGUI/dev-secrets/gemini-key.dpapi`. Dieser Speicher ist durch
Windows DPAPI an das Windows-Benutzerkonto gebunden. Der Klartext wird nur als
`GEMINI_API_KEY` an den Kindprozess übergeben und danach aus der Launcher-Umgebung
entfernt beziehungsweise durch den vorherigen Wert ersetzt. Eine bereits gesetzte
`GEMINI_API_KEY` hat Vorrang. Schlüsselwerte gehören nicht in Git oder Berichte.
Lokale Graphabfragen und der Quellgraph-Builder entschlüsseln den Key nicht.

```powershell
# Synthetischer Verbindungstest am offiziellen Google-Endpunkt:
powershell -NoProfile -ExecutionPolicy Bypass -File tools/graphify.ps1 -CheckGemini

# Lokale Graphabfrage:
powershell -NoProfile -ExecutionPolicy Bypass -File tools/graphify.ps1 query SourcePathResolver --graph graphify-out/graph.json --budget 1200
```

Die Ausführungsoption gilt nur für diesen PowerShell-Prozess und ändert keine
systemweite Skriptrichtlinie. Der Verbindungstest verwendet Graphifys konfiguriertes
Gemini-Modell (`GRAPHIFY_GEMINI_MODEL`, ansonsten der Paketstandard) und ausschließlich
einen kurzen synthetischen Prompt. Er verbraucht eine kleine API-Anfrage.
Der Architekturgraph und seine Abfragen benötigen keinen API-Aufruf.

## Graph ausschließlich aus Paketquellen

Der gepflegte Builder liegt in `tools/build_source_graph.py`. Er verwendet eine
explizite Python-Dateiliste aus den drei Paketen sowie temporäre AST- und
Exportverzeichnisse. Unter Windows startet ihn der Launcher im isolierten
Graphify-Interpreter:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/graphify.ps1 -Build
```

Unter Linux/macOS:

```bash
uv tool run --from 'graphifyy[gemini]==0.9.53' python tools/build_source_graph.py
```

Dieser Ablauf erzeugt beziehungsweise ersetzt `graphify-out/graph.json`,
`graphify-out/graph.html` und `graphify-out/GRAPH_REPORT.md`. Über 5.000 Knoten
meldet der HTML-Exporter die Größenüberschreitung und versucht eine aggregierte
Communityansicht; JSON und Bericht behalten den vollständigen Codegraphen.
Der zusätzliche temporäre HTML-Pfad verhindert das automatische Einlesen alter
Learning-Sidecars, auch bei dieser Aggregation. Eine fehlgeschlagene Ausgabe
bricht das Skript ab; dann keinen neuen Fingerprint als erfolgreichen Abschluss
erfassen.

`built_at_commit` bezeichnet `HEAD`; bei lokalen Änderungen beschreibt der Graph
zusätzlich den aktuellen Arbeitsbaum und ist kein Nachweis eines sauberen Commits.
AST-Beziehungen bilden den statisch erkennbaren Code ab; dynamische Aufrufe und
fachliche Zusammenhänge können fehlen. Bericht und Graph ersetzen deshalb
weder Quellprüfung noch Tests. Referenzierte Symbole können als Knoten ohne
`source_file` erscheinen; das bedeutet nicht, dass zusätzliche Dateien als
Eingaben gelesen wurden.

Abfragen verwenden den erzeugten Rootgraphen, beispielsweise:

```bash
graphify query "SourcePathResolver" --graph graphify-out/graph.json --budget 1200
```

Die lokale CLI-Abfrage benötigt keinen Modellanbieter. Ergebnisse anhand von
`source_file` und `source_location` im zugehörigen Paketquellcode nachvollziehen;
keine Memory-/Reflection-Inhalte zur Beantwortung ergänzen. Die HTML-Datei lässt
sich direkt im Browser öffnen.

## Grenzen des vorhandenen Fingerprint-Helfers

`tools/graphify_refresh.py --check` prüft den getrackten Fingerprint in
`.graphify-source-state.json`. Dafür liest und hasht der Helfer alle vorhandenen
getrackten sowie nicht ignorierten ungetrackten Repositorydateien. Das ist ein
breiterer Umfang als der erlaubte Graph-Korpus. Der Check führt keine semantische
Analyse oder Übertragung durch, belegt aber auch keinen ausschließlich aus
Paketquellen erzeugten Graphen. Für eine Sitzung mit strikt begrenzten Lesezugriffen
ist dieser breite Check deshalb ungeeignet. `--record-only` schreibt denselben
breiten Fingerprint und setzt eine vorhandene Graphify-Installation voraus.

Die Standardaktualisierung des Helfers übergibt die Projektwurzel an Graphify;
auch `--full` begrenzt sich nicht auf die drei Paketquellverzeichnisse. Diese
Aufrufe sind kein Ersatz für die explizite Dateiliste oben und werden für den
hier geforderten Korpus nicht verwendet.

Wenn der breitere lokale Leseumfang zulässig ist, nach erfolgreichem Graphbau
und Abschluss der Repositoryänderungen den Fingerprint gesondert pflegen:

```bash
python tools/graphify_refresh.py --record-only
python tools/graphify_refresh.py --check
python tools/graphify_refresh.py --check --require-local-graph
```

`python` bezeichnet dabei den Projektinterpreter; `graphify` muss wie oben
installiert auffindbar sein. Der Quality-Workflow verwendet `--check` ohne lokale
Graphartefakte. Der Hashschritt verarbeitet Dateiinhalte ausschließlich lokal;
er erweitert weder den AST-Korpus noch führt er eine semantische Analyse aus.

Die zusätzliche Option `--require-local-graph` erwartet `graph.html`, `graph.json`
und `GRAPH_REPORT.md` direkt unter `graphify-out/`, einen gerichteten Graphen mit
allen drei Paketen sowie `built_at_commit == HEAD`. Der Ablauf oben erzeugt dieses
Layout. Nach einem neuen Commit den Codegraph erneut für den neuen `HEAD` bauen,
bevor dieser Commitbezug geprüft wird. Ein bestandener Fingerprint- oder
Layout-Check ist keine Datenschutzprüfung und kein Beleg für semantisch
vollständige oder fachlich richtige Beziehungen.
