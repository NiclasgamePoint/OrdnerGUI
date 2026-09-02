#!/usr/bin/env bash
# Local convenience start: daemonized server build + independent tray + client.

set -u
umask 077
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PAPAGUI_PYTHON="$PWD/.venv/bin/python"
elif [ -x "venv/bin/python" ]; then
  PAPAGUI_PYTHON="$PWD/venv/bin/python"
else
  echo "Keine virtuelle Umgebung gefunden (.venv oder venv)."
  exit 1
fi

export PYTHONPATH="$PWD/packages/contracts/src:$PWD/packages/server/src:$PWD/packages/client/src${PYTHONPATH:+:$PYTHONPATH}"
export PAPAGUI_INDEX_SERVER_URL="${PAPAGUI_INDEX_SERVER_URL:-http://127.0.0.1:8765}"
export PAPAGUI_CLIENT_DATA_ROOT="${PAPAGUI_CLIENT_DATA_ROOT:-$PWD/client-data}"
export PAPAGUI_SERVER_DATA_PATH="${PAPAGUI_SERVER_DATA_PATH:-$PWD/docker-server-data}"
export PAPAGUI_SERVER_CONFIG_PATH="${PAPAGUI_SERVER_CONFIG_PATH:-$PWD/docker-config}"
export PAPAGUI_SOURCE_ID="${PAPAGUI_SOURCE_ID:-primary}"
mkdir -p "$PAPAGUI_CLIENT_DATA_ROOT" "$PAPAGUI_SERVER_DATA_PATH/logs" "$PAPAGUI_SERVER_CONFIG_PATH"

protect_secret_file() {
  local secret_file="$1"
  if [ -e "$secret_file" ] && ! chmod 600 "$secret_file"; then
    echo "Sichere Dateirechte konnten nicht gesetzt werden: $secret_file" >&2
    exit 1
  fi
}

write_secret_file() {
  local secret_file="$1"
  local secret_value="$2"
  printf '%s' "$secret_value" | "$PAPAGUI_PYTHON" -c \
    'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding="utf-8")' \
    "$secret_file"
  protect_secret_file "$secret_file"
}

secret_from_file() {
  local secret_name="$1"
  local secret_file="$PAPAGUI_SERVER_CONFIG_PATH/$2"
  local current_value="${!secret_name:-}"
  protect_secret_file "$secret_file"
  if [ -z "$current_value" ]; then
    if [ ! -s "$secret_file" ]; then
      current_value="$($PAPAGUI_PYTHON -c 'import secrets; print(secrets.token_urlsafe(32), end="")')"
      write_secret_file "$secret_file" "$current_value"
    else
      current_value="$(<"$secret_file")"
    fi
    printf -v "$secret_name" '%s' "$current_value"
  else
    # Keep the mounted server secret and the token used by this client process
    # identical even when the caller supplied the value through the environment.
    write_secret_file "$secret_file" "$current_value"
  fi
}

secret_from_file PAPAGUI_API_TOKEN api-token
export PAPAGUI_API_TOKEN

# The generated development password remains in the ignored, mode-0600 file so
# it can still be entered in the tray. Only a mounted hash file reaches Docker.
protect_secret_file "$PAPAGUI_SERVER_CONFIG_PATH/admin-password"
if [ -z "${PAPAGUI_ADMIN_PASSWORD_HASH:-}" ]; then
  secret_from_file PAPAGUI_ADMIN_PASSWORD admin-password
  PAPAGUI_ADMIN_PASSWORD_VALUE="$PAPAGUI_ADMIN_PASSWORD"
  unset PAPAGUI_ADMIN_PASSWORD
  PAPAGUI_ADMIN_PASSWORD_HASH="$(printf '%s' "$PAPAGUI_ADMIN_PASSWORD_VALUE" | "$PAPAGUI_PYTHON" -m papagui_server hash-password --stdin)"
  unset PAPAGUI_ADMIN_PASSWORD_VALUE
fi
write_secret_file "$PAPAGUI_SERVER_CONFIG_PATH/admin-password-hash" "$PAPAGUI_ADMIN_PASSWORD_HASH"
unset PAPAGUI_ADMIN_PASSWORD PAPAGUI_ADMIN_PASSWORD_HASH

if [ -z "${PAPAGUI_SOURCE_PATH:-}" ]; then
  if [ -s "$PAPAGUI_SERVER_CONFIG_PATH/source-path" ]; then
    PAPAGUI_SOURCE_PATH="$(<"$PAPAGUI_SERVER_CONFIG_PATH/source-path")"
  else
    PAPAGUI_SOURCE_PATH="$PWD/Bauvorhaben"
  fi
fi
export PAPAGUI_SOURCE_PATH
export PAPAGUI_UID="${PAPAGUI_UID:-$(id -u)}"
export PAPAGUI_GID="${PAPAGUI_GID:-$(id -g)}"

if [ -z "${PAPAGUI_SOURCE_MAPPINGS:-}" ]; then
  PAPAGUI_SOURCE_MAPPINGS="$($PAPAGUI_PYTHON -c 'import json,sys; print(json.dumps({sys.argv[1]: {"linux": sys.argv[2], "macos": sys.argv[2]}}))' "$PAPAGUI_SOURCE_ID" "$PAPAGUI_SOURCE_PATH")"
  export PAPAGUI_SOURCE_MAPPINGS
fi

start_server_in_background() {
  PAPAGUI_SERVER_START_PID=""
  if [ "${PAPAGUI_SKIP_DOCKER:-0}" = "1" ]; then
    echo "Docker-Start wurde über PAPAGUI_SKIP_DOCKER=1 übersprungen."
    return
  fi
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose nicht verfügbar; der Client verwendet seinen letzten lokalen Stand."
    return
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Docker läuft nicht; der Client verwendet seinen letzten lokalen Stand."
    return
  fi
  if [ ! -d "$PAPAGUI_SOURCE_PATH" ]; then
    echo "Serverstart übersprungen: Datenquelle nicht gefunden: $PAPAGUI_SOURCE_PATH"
    return
  fi

  (
    docker compose -f deploy/server/compose.yaml up -d --build papagui-server
  ) >"$PAPAGUI_SERVER_DATA_PATH/logs/docker-server-startup.log" 2>&1 &
  PAPAGUI_SERVER_START_PID="$!"
  echo "PapaGUI-Server wird im Hintergrund gebaut und gestartet."
}

start_server_in_background

wait_for_server_health() {
  if [ -z "${PAPAGUI_SERVER_START_PID:-}" ]; then
    return
  fi
  echo "Warte auf den Server-Healthcheck …"
  attempt=0
  while [ "$attempt" -lt 600 ]; do
    if "$PAPAGUI_PYTHON" -c 'import json,sys,urllib.request; json.load(urllib.request.urlopen(sys.argv[1].rstrip("/")+"/health", timeout=1))' "$PAPAGUI_INDEX_SERVER_URL" >/dev/null 2>&1; then
      echo "PapaGUI-Server ist erreichbar."
      return
    fi
    if ! kill -0 "$PAPAGUI_SERVER_START_PID" 2>/dev/null; then
      wait "$PAPAGUI_SERVER_START_PID" 2>/dev/null || true
      if ! docker compose -f deploy/server/compose.yaml ps --status running --quiet papagui-server 2>/dev/null | grep -q .; then
        echo "Serverstart fehlgeschlagen; Details: $PAPAGUI_SERVER_DATA_PATH/logs/docker-server-startup.log"
        return
      fi
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  echo "Server-Healthcheck nach 10 Minuten noch nicht erfolgreich; Client startet offline."
}

wait_for_server_health

PAPAGUI_DISPLAY_PID=""
if [ "$(uname -s)" = "Linux" ] && [ -z "${DISPLAY:-}" ] && command -v Xvfb >/dev/null 2>&1; then
  export DISPLAY=:99
  Xvfb :99 -screen 0 1920x1080x24 >/dev/null 2>&1 &
  PAPAGUI_DISPLAY_PID="$!"
fi

"$PAPAGUI_PYTHON" -m papagui_client.entrypoints.tray --background \
  >"$PAPAGUI_SERVER_CONFIG_PATH/index-tray.log" 2>&1 &
"$PAPAGUI_PYTHON" -m papagui_client "$@"
PAPAGUI_EXIT_CODE=$?

if [ -n "$PAPAGUI_DISPLAY_PID" ]; then
  kill "$PAPAGUI_DISPLAY_PID" 2>/dev/null || true
fi
exit "$PAPAGUI_EXIT_CODE"
