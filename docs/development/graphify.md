# Graphify

Graphify erzeugt eine lokale Architekturkarte für das gesamte Monorepo. Das
Verzeichnis `graphify-out/` bleibt absichtlich in `.gitignore`; eingecheckt wird
nur ein kleiner Fingerprint der verarbeiteten Quellen.

## Installation

Das offizielle Paket heißt `graphifyy`, der Befehl weiterhin `graphify`:

```bash
uv tool install graphifyy
# alternativ: pipx install graphifyy
graphify install --platform codex
```

Graphify ist keine Client- oder Serverabhängigkeit. Für reinen Code ist kein
API-Key erforderlich. Dokumente können durch Codex semantisch extrahiert werden;
optional unterstützt Graphify `GEMINI_API_KEY` beziehungsweise
`GOOGLE_API_KEY`.

## Aktualisierung

```bash
.venv/bin/python tools/graphify_refresh.py
.venv/bin/python tools/graphify_refresh.py --check
```

Nach großen Verschiebungen oder Löschungen wird in Codex zuerst
`/graphify . --directed` ausgeführt. Anschließend wird der Fingerprint erfasst:

```bash
.venv/bin/python tools/graphify_refresh.py --record-only
```

`tools/graphify_refresh.py --full --force` bleibt als rein struktureller,
plattformneutraler Code-Fallback verfügbar. Für die beiden Komponententags ist
jedoch der gerichtete Skill-Lauf maßgeblich, weil er auch Dokumentation und
Beziehungsrichtung erfasst.

Nach einem vollständigen semantischen Skill-Lauf schreibt
`--record-only` den neuen Quell-Fingerprint. `--require-local-graph` prüft vor
einem lokalen Tag zusätzlich, ob HTML, JSON und Bericht vorhanden sind, der
Graph gerichtet ist, alle drei Pakete enthält und exakt für `HEAD` gebaut
wurde.

```bash
.venv/bin/python tools/graphify_refresh.py --record-only
.venv/bin/python tools/graphify_refresh.py --check --require-local-graph
```

Die interaktive Ansicht liegt anschließend unter
`graphify-out/graph.html`. Lokal kann sie so bereitgestellt werden:

```bash
python -m http.server 8000 --directory graphify-out
```
