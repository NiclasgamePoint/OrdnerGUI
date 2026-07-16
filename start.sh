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

# Virtuellen Display starten (falls kein echter Display vorhanden)
if [ -z "$DISPLAY" ]; then
	export DISPLAY=:99
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

# Start GUI
python main.py

# Aufräumen
[ -n "$XVFB_PID" ] && kill $XVFB_PID $X11VNC_PID $NOVNC_PID 2>/dev/null
