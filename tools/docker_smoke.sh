#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${PAPAGUI_SMOKE_IMAGE:-papagui-server:0.4.2-smoke}"
CONTAINER="papagui-server-smoke-$$"
SMOKE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/papagui-server-smoke.XXXXXX")"
SOURCE_ROOT="${SMOKE_ROOT}/source"
DATA_ROOT="${SMOKE_ROOT}/data"
CONFIG_ROOT="${SMOKE_ROOT}/config"
TOKEN="smoke-client-token-123"
EVENT_LOG="${SMOKE_ROOT}/docker-events.jsonl"
EVENT_PID=""

cleanup() {
    if [[ -n "${EVENT_PID}" ]]; then
        kill "${EVENT_PID}" >/dev/null 2>&1 || true
        wait "${EVENT_PID}" >/dev/null 2>&1 || true
    fi
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    case "${SMOKE_ROOT}" in
        "${TMPDIR:-/tmp}"/papagui-server-smoke.*)
            rm -rf -- "${SMOKE_ROOT}"
            ;;
    esac
}
trap cleanup EXIT

mkdir -p "${SOURCE_ROOT}/Planung/2026/Testkunde, Berlin" "${DATA_ROOT}" "${CONFIG_ROOT}"
printf '%s\n' "Smoke-Test-Dokument" > "${SOURCE_ROOT}/Planung/2026/Testkunde, Berlin/test.txt"

if [[ "${PAPAGUI_SMOKE_SKIP_BUILD:-0}" != "1" ]]; then
    docker build \
        -f "${PROJECT_ROOT}/deploy/server/Dockerfile" \
        -t "${IMAGE}" \
        "${PROJECT_ROOT}"
fi

docker compose -f "${PROJECT_ROOT}/deploy/server/compose.yaml" config --quiet

PASSWORD_HASH="$(printf '%s' 'smoke-admin-password-123' | docker run --rm -i "${IMAGE}" hash-password --stdin)"
[[ "${PASSWORD_HASH}" == \$argon2id\$* ]]
printf '%s' "${TOKEN}" > "${CONFIG_ROOT}/api-token"
printf '%s' "${PASSWORD_HASH}" > "${CONFIG_ROOT}/admin-password-hash"
chmod 600 "${CONFIG_ROOT}/api-token" "${CONFIG_ROOT}/admin-password-hash"
docker run --rm --entrypoint tesseract "${IMAGE}" --version >/dev/null

run_index_once() {
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -e "PAPAGUI_API_TOKEN_FILE=/config/api-token" \
        -e "PAPAGUI_ADMIN_PASSWORD_HASH_FILE=/config/admin-password-hash" \
        -v "${SOURCE_ROOT}:/source:ro" \
        -v "${DATA_ROOT}:/data" \
        -v "${CONFIG_ROOT}:/config" \
        "${IMAGE}" once "$@"
}

start_container() {
    docker run -d \
        --name "${CONTAINER}" \
        --init \
        --restart unless-stopped \
        --user "$(id -u):$(id -g)" \
        -e "PAPAGUI_API_TOKEN_FILE=/config/api-token" \
        -e "PAPAGUI_ADMIN_PASSWORD_HASH_FILE=/config/admin-password-hash" \
        -v "${SOURCE_ROOT}:/source:ro" \
        -v "${DATA_ROOT}:/data" \
        -v "${CONFIG_ROOT}:/config" \
        "${IMAGE}" serve --no-run-on-start >/dev/null
}

wait_until_ready() {
    local attempt
    for attempt in $(seq 1 60); do
        if docker exec "${CONTAINER}" python -c \
            "import json,urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2))" \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    docker logs "${CONTAINER}" >&2 || true
    return 1
}

generation_id() {
    docker exec "${CONTAINER}" python -c \
        "import json,urllib.request; r=urllib.request.Request('http://127.0.0.1:8765/v2/generations/current', headers={'Authorization':'Bearer ${TOKEN}'}); print(json.load(urllib.request.urlopen(r, timeout=3))['components']['index']['generation'])"
}

component_archive_count() {
    find "${DATA_ROOT}/generations-v2/$1/archives" -maxdepth 1 -type f -name '*.zip' \
        | wc -l | tr -d '[:space:]'
}

run_index_once --full-rebuild
printf '%s\n' "Zweiter persistenter Lauf" \
    > "${SOURCE_ROOT}/Planung/2026/Testkunde, Berlin/zweiter-lauf.txt"
run_index_once

start_container
wait_until_ready

CONTAINER_ENVIRONMENT="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${CONTAINER}")"
grep -q '^PAPAGUI_API_TOKEN_FILE=/config/api-token$' <<<"${CONTAINER_ENVIRONMENT}"
grep -q '^PAPAGUI_ADMIN_PASSWORD_HASH_FILE=/config/admin-password-hash$' <<<"${CONTAINER_ENVIRONMENT}"
if grep -Fq "${TOKEN}" <<<"${CONTAINER_ENVIRONMENT}" || grep -Fq "${PASSWORD_HASH}" <<<"${CONTAINER_ENVIRONMENT}"; then
    echo "Ein Klartext-Secret ist in docker inspect sichtbar." >&2
    exit 1
fi

if docker exec "${CONTAINER}" touch /source/must-not-be-written 2>/dev/null; then
    echo "Source-Mount ist unerwartet beschreibbar." >&2
    exit 1
fi

docker exec "${CONTAINER}" python -c \
    "import importlib.util; assert importlib.util.find_spec('PySide6') is None; assert importlib.util.find_spec('papagui_client') is None; assert importlib.util.find_spec('app') is None"

for attempt in $(seq 1 60); do
    if GENERATION_BEFORE="$(generation_id 2>/dev/null)"; then
        break
    fi
    sleep 1
done
test -n "${GENERATION_BEFORE:-}"
test -s "${DATA_ROOT}/index/catalog/active.db"
test -s "${DATA_ROOT}/customers.db"
INDEX_ARCHIVES_BEFORE="$(component_archive_count index)"
CUSTOMER_ARCHIVES_BEFORE="$(component_archive_count customers)"
test "${INDEX_ARCHIVES_BEFORE}" -ge 2
test "${CUSTOMER_ARCHIVES_BEFORE}" -ge 2
CUSTOMER_DATABASE_HASH_BEFORE="$(sha256sum "${DATA_ROOT}/customers.db" | awk '{print $1}')"

# Exercise backup and restore on disposable copies while leaving live data intact.
docker run --rm \
    --user "$(id -u):$(id -g)" \
    --entrypoint python \
    -v "${DATA_ROOT}:/data" \
    "${IMAGE}" -c '
from pathlib import Path
import sqlite3

from papagui_server.adapters.generations import snapshot_sqlite
from papagui_server.adapters.migration import restore_customer_database

source = Path("/data/customers.db")
root = Path("/data/restore-smoke")
backup = root / "customers.backup.db"
restored = root / "customers.restored.db"
snapshot_sqlite(source, backup)
restore_customer_database(restored, backup)
connections = [
    sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    for path in (source, backup, restored)
]
try:
    counts = [connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0] for connection in connections]
    assert len(set(counts)) == 1
    assert all(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok" for connection in connections)
finally:
    for connection in connections:
        connection.close()
'
test "$(sha256sum "${DATA_ROOT}/customers.db" | awk '{print $1}')" = \
    "${CUSTOMER_DATABASE_HASH_BEFORE}"

test "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "${CONTAINER}")" = "unless-stopped"
RESTART_COUNT_BEFORE="$(docker inspect --format '{{.RestartCount}}' "${CONTAINER}")"
ADMIN_SESSION_BEFORE="$(docker exec "${CONTAINER}" python -c \
    "import json,urllib.request; payload=json.dumps({'password':'smoke-admin-password-123'}).encode(); request=urllib.request.Request('http://127.0.0.1:8765/v2/admin/session', data=payload, headers={'Authorization':'Bearer ${TOKEN}','Content-Type':'application/json'}, method='POST'); print(json.load(urllib.request.urlopen(request, timeout=3))['token'])")"
test -n "${ADMIN_SESSION_BEFORE}"

# Docker arms restart policies only after a container has stayed up successfully.
RESTART_POLICY_ARMED=""
for attempt in $(seq 1 20); do
    if docker exec "${CONTAINER}" python -c \
        "import json,urllib.request; request=urllib.request.Request('http://127.0.0.1:8765/v2/server/status', headers={'Authorization':'Bearer ${TOKEN}'}); assert json.load(urllib.request.urlopen(request, timeout=3))['uptime_seconds'] >= 10" \
        >/dev/null 2>&1; then
        RESTART_POLICY_ARMED="yes"
        break
    fi
    sleep 1
done
test "${RESTART_POLICY_ARMED}" = "yes"

docker events \
    --filter "container=${CONTAINER}" \
    --filter event=die \
    --format '{{json .Actor.Attributes}}' > "${EVENT_LOG}" &
EVENT_PID="$!"
sleep 1

docker exec "${CONTAINER}" python -c \
    "import json,urllib.request; base='http://127.0.0.1:8765'; common={'Authorization':'Bearer ${TOKEN}','X-PapaGUI-Admin-Session':'${ADMIN_SESSION_BEFORE}','Content-Type':'application/json'}; payload=json.dumps({'settings':{'interval_seconds':900}}).encode(); request=urllib.request.Request(base+'/v2/admin/settings', data=payload, headers=common, method='PUT'); assert json.load(urllib.request.urlopen(request, timeout=3))['settings']['interval_seconds']==900; request=urllib.request.Request(base+'/v2/admin/server/restart', data=b'{}', headers=common, method='POST'); assert json.load(urllib.request.urlopen(request, timeout=3))['restart'] is True"

for attempt in $(seq 1 60); do
    RESTART_COUNT_AFTER="$(docker inspect --format '{{.RestartCount}}' "${CONTAINER}" 2>/dev/null || true)"
    RUNNING_AFTER="$(docker inspect --format '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || true)"
    if [[ "${RESTART_COUNT_AFTER:-0}" -gt "${RESTART_COUNT_BEFORE}" && "${RUNNING_AFTER}" == "true" ]]; then
        break
    fi
    sleep 1
done
test "${RESTART_COUNT_AFTER:-0}" -gt "${RESTART_COUNT_BEFORE}"
wait_until_ready

for attempt in $(seq 1 20); do
    if grep -Eq '"exitCode"[[:space:]]*:[[:space:]]*"75"' "${EVENT_LOG}"; then
        break
    fi
    sleep 0.25
done
if ! grep -Eq '"exitCode"[[:space:]]*:[[:space:]]*"75"' "${EVENT_LOG}"; then
    echo "Kein kontrollierter Container-Exitcode 75 beobachtet." >&2
    cat "${EVENT_LOG}" >&2 || true
    docker logs "${CONTAINER}" >&2 || true
    exit 1
fi
kill "${EVENT_PID}" >/dev/null 2>&1 || true
wait "${EVENT_PID}" >/dev/null 2>&1 || true
EVENT_PID=""

# Admin sessions are process-local, while settings and generations survive restart.
docker exec "${CONTAINER}" python -c \
    "
import urllib.error
import urllib.request

request = urllib.request.Request(
    'http://127.0.0.1:8765/v2/admin/settings',
    headers={
        'Authorization': 'Bearer ${TOKEN}',
        'X-PapaGUI-Admin-Session': '${ADMIN_SESSION_BEFORE}',
    },
)
try:
    urllib.request.urlopen(request, timeout=3)
    raise AssertionError('old admin session survived restart')
except urllib.error.HTTPError as error:
    assert error.code == 403
"
docker exec "${CONTAINER}" python -c \
    "import json,urllib.request; base='http://127.0.0.1:8765'; payload=json.dumps({'password':'smoke-admin-password-123'}).encode(); login=urllib.request.Request(base+'/v2/admin/session', data=payload, headers={'Authorization':'Bearer ${TOKEN}','Content-Type':'application/json'}, method='POST'); session=json.load(urllib.request.urlopen(login, timeout=3))['token']; settings=urllib.request.Request(base+'/v2/admin/settings', headers={'Authorization':'Bearer ${TOKEN}','X-PapaGUI-Admin-Session':session}); assert json.load(urllib.request.urlopen(settings, timeout=3))['settings']['interval_seconds']==900"

GENERATION_AFTER="$(generation_id)"
test "${GENERATION_AFTER}" = "${GENERATION_BEFORE}"
test "$(component_archive_count index)" = "${INDEX_ARCHIVES_BEFORE}"
test "$(component_archive_count customers)" = "${CUSTOMER_ARCHIVES_BEFORE}"

echo "PapaGUI server Docker smoke test passed (${GENERATION_AFTER}, controlled restart exit 75)."
