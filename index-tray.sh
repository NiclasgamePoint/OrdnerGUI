#!/bin/bash
# Standalone PapaGUI index-server tray, independent from the main window.

cd "$(dirname "$0")"

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

if [ -z "$PAPAGUI_API_TOKEN" ] && [ -f "$PAPAGUI_CONFIG_PATH/api-token" ]; then
	PAPAGUI_API_TOKEN=$(<"$PAPAGUI_CONFIG_PATH/api-token")
	export PAPAGUI_API_TOKEN
fi

if [ -L "$PAPAGUI_CLIENT_ROOT/current" ]; then
	export PAPAGUI_DATA_DIR="$PAPAGUI_CLIENT_ROOT/current"
else
	export PAPAGUI_DATA_DIR="$PWD/data"
fi

exec python -m app.index_tray_app "$@"
