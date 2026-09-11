#!/usr/bin/env python3
"""Build the local architecture graph from package Python sources only.

Run with the isolated Graphify interpreter, via tools/graphify.ps1 -Build on Windows.
"""

from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory

from graphify.extract import extract
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json, to_html

root = Path(__file__).resolve().parents[1]
files = sorted(
    path
    for package in ("contracts", "server", "client")
    for path in (root / "packages" / package / "src").rglob("*.py")
    if "__pycache__" not in path.parts
)
if not files:
    raise SystemExit("No Python package sources found in the PapaGUI repository.")
with TemporaryDirectory(prefix="papagui-code-ast-") as cache:
    extraction = extract(files, root=root, cache_root=Path(cache), parallel=False)
graph = build_from_json(extraction, root=root, directed=True)
communities = cluster(graph)
labels = {key: f"Code community {key}" for key in communities}
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
output = root / "graphify-out"
output.mkdir(parents=True, exist_ok=True)
(output / ".graphify_python").write_text(sys.executable, encoding="utf-8")
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
    if len(graph) > 5000:
        print(f"Graph has {len(graph)} nodes; HTML will use an aggregated community view.")
    html = staged / "graph.html"
    if not to_html(graph, communities, str(html), community_labels=labels,
                   node_limit=5000, learning_overlay={}):
        raise SystemExit("Graphify did not produce an HTML view.")
    for name in ("graph.json", "graph.html", "GRAPH_REPORT.md"):
        (output / name).write_bytes((staged / name).read_bytes())
print(f"Architecture graph: {len(files)} files, {len(graph)} nodes, {graph.number_of_edges()} edges")
