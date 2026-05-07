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

# Start GUI
python main.py
