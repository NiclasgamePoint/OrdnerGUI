#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
else
  echo "Keine virtuelle Umgebung gefunden (.venv oder venv)."
  exit 1
fi

python main.py
