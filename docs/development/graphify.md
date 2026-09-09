# Graphify

Graphify dient hier ausschließlich als lokale Architekturkarte des Pythoncodes
in `packages/{contracts,server,client}/src/**/*.py`. Kundendokumente,
Datenbanken, Logs, produktive Konfigurationen, historische Pläne und Memories
gehören nicht zum Korpus. Der Pfad `graphify-out/` ist Git-ignoriert.

## Installation

Das Paket heißt `graphifyy`, der Befehl `graphify`. Der Versionsprüfungsworkflow
verwendet derzeit 0.9.53; die folgende Anleitung verwendet dieselbe Version:

```bash
uv tool install graphifyy==0.9.53
```

Graphify ist keine Client- oder Serverabhängigkeit. Die strukturelle
AST-Extraktion benötigt weder API-Key noch Modellanbieter. Eine semantische
Dokumentextraktion ist für dieses Projekt nicht Teil des Ablaufs.

## Graph ausschließlich aus Paketquellen

Den folgenden Block nach Anlegen von `graphify-out/` als
`graphify-out/build_source_graph.py` speichern. Er verwendet eine explizite
Dateiliste und einen neuen temporären AST-Cache, damit vorhandene Graph-Caches
oder Memories nicht als Eingang dienen. Vom Repositoryverzeichnis aus ausführen.

```python
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from graphify.extract import extract
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json, to_html

root = Path.cwd()
files = sorted(
    path
    for package in ("contracts", "server", "client")
    for path in (root / "packages" / package / "src").rglob("*.py")
    if "__pycache__" not in path.parts
)
if not files:
    raise SystemExit("Run this script from the PapaGUI repository root.")
with TemporaryDirectory(prefix="papagui-code-ast-") as cache:
    extraction = extract(files, root=root, cache_root=Path(cache), parallel=False)
graph = build_from_json(extraction, root=root, directed=True)
communities = cluster(graph)
labels = {key: f"Code community {key}" for key in communities}
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
output = root / "graphify-out"
output.mkdir(parents=True, exist_ok=True)
detection = {
    "total_files": len(files),
    "total_words": sum(len(path.read_text(encoding="utf-8").split()) for path in files),
    "files": {"code": [str(path.relative_to(root)) for path in files]},
}
report = generate(
    graph, communities, score_all(graph, communities), labels,
    god_nodes(graph), surprising_connections(graph, communities),
    detection, {"input": 0, "output": 0}, str(root),
    suggested_questions=suggest_questions(graph, communities, labels),
    built_at_commit=head, learning={},
)
scope = "Source scope: packages/{contracts,server,client}/src/**/*.py only. "
scope += "No customer data, documents, memories or semantic extraction.\n\n"
# Stage all exports; neither JSON nor HTML should inspect previous sidecars.
with TemporaryDirectory(prefix="papagui-code-export-") as temporary:
    staged = Path(temporary)
    if not to_json(graph, communities, str(staged / "graph.json"),
                   force=True, built_at_commit=head, community_labels=labels):
        raise SystemExit("Graphify did not produce a JSON graph.")
    (staged / "GRAPH_REPORT.md").write_text(scope + report, encoding="utf-8")
    html = staged / "graph.html"
    if not to_html(graph, communities, str(html), community_labels=labels,
                   node_limit=5000, learning_overlay={}):
        raise SystemExit("Graphify did not produce an HTML view.")
    for name in ("graph.json", "graph.html", "GRAPH_REPORT.md"):
        (output / name).write_bytes((staged / name).read_bytes())
```

```bash
uv tool run --from graphifyy==0.9.53 python graphify-out/build_source_graph.py
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
