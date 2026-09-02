#!/bin/bash
# PapaGUI Startup-Script

cd "$(dirname "$0")"

# Activate virtual environment (.venv preferred, then venv)
if [ -f ".venv/bin/activate" ]; then
	source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
	source venv/bin/activate
else
	echo "Keine virtuelle Umgebung gefunden (.venv oder venv)."
	exit 1
fi

export PAPAGUI_EXTERNAL_INDEXER=1
export PAPAGUI_INDEX_SERVER_URL="${PAPAGUI_INDEX_SERVER_URL:-http://127.0.0.1:8765}"
export PAPAGUI_CLIENT_ROOT="${PAPAGUI_CLIENT_ROOT:-$PWD/client-data}"
export PAPAGUI_CONFIG_PATH="${PAPAGUI_CONFIG_PATH:-$PWD/docker-config}"
mkdir -p "$PAPAGUI_CONFIG_PATH"
if [ -z "$PAPAGUI_API_TOKEN" ]; then
	TOKEN_FILE="$PAPAGUI_CONFIG_PATH/api-token"
	if [ ! -f "$TOKEN_FILE" ]; then
		python -c 'import secrets,sys; open(sys.argv[1], "w", encoding="utf-8").write(secrets.token_urlsafe(32))' "$TOKEN_FILE"
		chmod 600 "$TOKEN_FILE"
	fi
	PAPAGUI_API_TOKEN=$(<"$TOKEN_FILE")
	export PAPAGUI_API_TOKEN
fi

# Docker owns catalog, customer recognition and content indexing. The GUI only
# reads the persistent result, so both processes never write the same index.
start_docker_indexer() {
	if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
		echo "Docker Compose nicht verfügbar – PapaGUI startet ohne Docker-Indexer."
		return
	fi

	if ! docker info >/dev/null 2>&1; then
		echo "Docker läuft nicht – PapaGUI startet ohne Docker-Indexer."
		return
	fi

	if [ -z "$PAPAGUI_SOURCE_PATH" ]; then
		PAPAGUI_SOURCE_PATH=$(python -c 'from app.core.config import get_configured_index_source; print(get_configured_index_source().resolve())')
	fi
	if [ ! -d "$PAPAGUI_SOURCE_PATH" ]; then
		echo "Docker-Indexer wartet: Datenquelle nicht gefunden: $PAPAGUI_SOURCE_PATH"
		return
	fi

	export PAPAGUI_SOURCE_PATH
	export PAPAGUI_INDEX_PATH="${PAPAGUI_INDEX_PATH:-$PWD/docker-server-data}"
	export PAPAGUI_UID="${PAPAGUI_UID:-$(id -u)}"
	export PAPAGUI_GID="${PAPAGUI_GID:-$(id -g)}"
	mkdir -p "$PAPAGUI_INDEX_PATH/logs" "$PAPAGUI_CONFIG_PATH"

	(
		if docker buildx version >/dev/null 2>&1; then
			docker compose up -d --build indexer
		else
			echo "Docker Buildx fehlt – verwende den klassischen Docker-Builder."
			DOCKER_BUILDKIT=0 docker build -t papagui-indexer:0.4 . && \
				docker compose up -d --no-build indexer
		fi
	) >"$PAPAGUI_INDEX_PATH/logs/docker-indexer-startup.log" 2>&1 &
	echo "Docker-Indexer wird im Hintergrund gebaut und gestartet."
}

start_docker_indexer

# Pull the newest complete generation. If the server is unavailable, the last
# verified local generation remains active. Existing v0.4 data seeds the cache
# exactly once during migration.
if python -c 'import os,urllib.request; request=urllib.request.Request(os.environ["PAPAGUI_INDEX_SERVER_URL"].rstrip("/")+"/v1/index/current"); token=os.environ.get("PAPAGUI_API_TOKEN", ""); token and request.add_header("Authorization", "Bearer "+token); urllib.request.urlopen(request, timeout=1).close()' 2>/dev/null; then
	python -m app.services.index_client \
		--server "$PAPAGUI_INDEX_SERVER_URL" \
		--client-root "$PAPAGUI_CLIENT_ROOT" \
		--bootstrap-from "$PWD/data" \
		--timeout-seconds 3 || true
else
	echo "Docker-Indexer erstellt noch die erste Generation – Synchronisation folgt automatisch."
	python -m app.services.index_client \
		--server "$PAPAGUI_INDEX_SERVER_URL" \
		--client-root "$PAPAGUI_CLIENT_ROOT" \
		--bootstrap-from "$PWD/data" \
		--timeout-seconds 0.01 >/dev/null 2>&1 || true
fi

if [ -L "$PAPAGUI_CLIENT_ROOT/current" ]; then
	export PAPAGUI_DATA_DIR="$PAPAGUI_CLIENT_ROOT/current"
else
	echo "Keine gültige lokale Indexgeneration vorhanden – verwende Altbestand."
	export PAPAGUI_DATA_DIR="$PWD/data"
fi

# Virtuellen Display starten (falls kein echter Display vorhanden)
if [ -z "$DISPLAY" ]; then
	export DISPLAY=:99
	export PAPAGUI_FORCE_FULLSCREEN=1
	Xvfb :99 -screen 0 1920x1080x24 &
	XVFB_PID=$!
	echo "Virtueller Display gestartet (PID $XVFB_PID)"

	# noVNC starten, damit die GUI im Browser erreichbar ist (Port 6080)
	x11vnc -display :99 -forever -nopw -quiet &
	X11VNC_PID=$!
	websockify --web /usr/share/novnc 6080 localhost:5900 &
	NOVNC_PID=$!
	echo "GUI erreichbar unter: http://localhost:6080/vnc.html"
fi

# The index-server tray is its own process. It therefore remains available if
# the main window is closed and later launches only activate the existing tray.
python -m app.index_tray_app --background >"$PAPAGUI_CONFIG_PATH/index-tray.log" 2>&1 &

# Start GUI
python main.py

# Aufräumen
[ -n "$XVFB_PID" ] && kill $XVFB_PID $X11VNC_PID $NOVNC_PID 2>/dev/null
