#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PAPAGUI_PYTHON="$PWD/.venv/bin/python"
elif [ -x "venv/bin/python" ]; then
  PAPAGUI_PYTHON="$PWD/venv/bin/python"
else
  echo "Keine virtuelle Umgebung gefunden (.venv oder venv)."
  exit 1
fi

export PYTHONPATH="$PWD/packages/contracts/src:$PWD/packages/client/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PAPAGUI_PYTHON" -m papagui_client.entrypoints.tray "$@"
