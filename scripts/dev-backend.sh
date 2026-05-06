#!/usr/bin/env bash
# scripts/dev-backend.sh
#
# Dev backend supervisor — starts uvicorn (reload mode) and watches the
# /health endpoint. If health flatlines for ${UNHEALTHY_LIMIT} consecutive
# probes the worker gets killed and respawned. Replaces the manual "find
# my dead backend, kill -9, restart" loop that came up every few hours
# during D7+D8 development.
#
# Why this exists (vs. relying on uvicorn's own --reload):
#   - DBOS runs in-process with FastAPI; an unhandled exception in a
#     workflow thread can wedge uvicorn's event loop without crashing
#     the process — uvicorn won't restart what didn't crash
#   - reload teardown sometimes leaves DBOS queue listener threads
#     holding sockets, which the new worker can't bind
#   - net result: backend appears alive (process running, port open)
#     but every HTTP request times out
#
# This script polls /health every PROBE_INTERVAL seconds, after
# UNHEALTHY_LIMIT consecutive failures it kill -9's the worker and the
# inner `while true` loop respawns it. Logs go to /tmp/dev-backend.log.
#
# Usage:
#   ./scripts/dev-backend.sh             # foreground, Ctrl-C to stop
#   nohup ./scripts/dev-backend.sh &     # background

set -u

PORT="${BACKEND_PORT:-8082}"
HEALTH_URL="http://localhost:${PORT}/health"
LOG_FILE="${BACKEND_LOG:-/tmp/dev-backend.log}"
PROBE_INTERVAL="${PROBE_INTERVAL:-30}"
UNHEALTHY_LIMIT="${UNHEALTHY_LIMIT:-3}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-5}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/../backend"
ENV_FILE="${BACKEND_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[dev-backend] missing ${ENV_FILE}" >&2
  exit 1
fi

cleanup() {
  echo "[dev-backend] shutting down (parent pid=$$)"
  pkill -P $$ 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

start_uvicorn() {
  echo "[dev-backend] starting uvicorn on :${PORT} (logs → ${LOG_FILE})"
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
  cd "${BACKEND_DIR}" || exit 1
  uv run uvicorn app.main:app \
    --host 0.0.0.0 --port "${PORT}" \
    --reload --reload-dir app \
    >> "${LOG_FILE}" 2>&1 &
  UVICORN_PID=$!
  echo "[dev-backend] uvicorn pid=${UVICORN_PID}"
}

kill_uvicorn() {
  echo "[dev-backend] killing uvicorn pid=${UVICORN_PID}"
  # SIGKILL the worker tree — graceful shutdown is what got us into
  # this mess (DBOS.destroy() blocking) so don't bother with SIGTERM.
  pkill -9 -P "${UVICORN_PID}" 2>/dev/null || true
  kill -9 "${UVICORN_PID}" 2>/dev/null || true
  # Anything still holding the port (orphans from previous runs)
  lsof -ti tcp:"${PORT}" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  sleep 2
}

probe() {
  curl -s -o /dev/null -w "%{http_code}" --max-time "${PROBE_TIMEOUT}" \
    "${HEALTH_URL}" 2>/dev/null
}

while true; do
  start_uvicorn

  # Warm-up: wait up to 90 s for /health to come up before policing it.
  warmup_deadline=$(( $(date +%s) + 90 ))
  while (( $(date +%s) < warmup_deadline )); do
    if [[ "$(probe)" == "200" ]]; then
      echo "[dev-backend] healthy"
      break
    fi
    sleep 2
  done

  failures=0
  while true; do
    sleep "${PROBE_INTERVAL}"
    code=$(probe)
    if [[ "${code}" == "200" ]]; then
      failures=0
      continue
    fi
    failures=$(( failures + 1 ))
    echo "[dev-backend] unhealthy (probe ${failures}/${UNHEALTHY_LIMIT}, code=${code:-timeout})"
    if (( failures >= UNHEALTHY_LIMIT )); then
      echo "[dev-backend] limit reached → restarting"
      break
    fi
  done

  kill_uvicorn
  echo "[dev-backend] respawning in 2s"
  sleep 2
done
