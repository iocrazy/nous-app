#!/usr/bin/env bash
# scripts/dev-backend.sh
#
# Dev backend supervisor — runs uvicorn under watchexec for clean restarts
# and polls /healthz to catch wedged-but-not-crashed states.
#
# Why we don't use uvicorn's own --reload:
#   - DBOS / async sweepers / SsrfProxy hold sockets/threads on shutdown.
#     uvicorn's reload sends SIGTERM to the worker but its hard-coded
#     graceful timeout (and lack of cancel-safe coordination with our
#     async background tasks) leaves listeners on :PORT in CLOSED state.
#     Net result: process running, port open, every request times out.
#   - watchexec spawns a fresh process group on every change. SIGTERM →
#     5 s grace → SIGKILL → bind brand-new socket. Predictable.
#
# Two layers of safety:
#   1. watchexec restarts on app/ file changes (code edits)
#   2. supervisor probes /healthz every PROBE_INTERVAL; if 3 consecutive
#      failures (wedged process, no file change to trigger restart),
#      kills the watchexec tree and restarts it.
#
# Logs go to /tmp/dev-backend.log (override with BACKEND_LOG=...).
#
# Usage:
#   ./scripts/dev-backend.sh             # foreground, Ctrl-C to stop
#   nohup ./scripts/dev-backend.sh &     # background

set -u

PORT="${BACKEND_PORT:-8082}"
HEALTH_URL="http://localhost:${PORT}/api/v1/healthz"
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

if ! command -v watchexec >/dev/null 2>&1; then
  echo "[dev-backend] watchexec not installed. Run: brew install watchexec" >&2
  exit 1
fi

cleanup() {
  echo "[dev-backend] shutting down (parent pid=$$)"
  if [[ -n "${WATCHEXEC_PID:-}" ]]; then
    pkill -TERM -P "${WATCHEXEC_PID}" 2>/dev/null || true
    kill -TERM "${WATCHEXEC_PID}" 2>/dev/null || true
    sleep 2
    pkill -KILL -P "${WATCHEXEC_PID}" 2>/dev/null || true
    kill -KILL "${WATCHEXEC_PID}" 2>/dev/null || true
  fi
  lsof -ti tcp:"${PORT}" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

start_watchexec() {
  echo "[dev-backend] starting watchexec → uvicorn on :${PORT} (logs → ${LOG_FILE})"
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
  cd "${BACKEND_DIR}" || exit 1

  # watchexec flags:
  #   --watch app                    only watch app/, not logs/ or .venv
  #   --exts py                      only restart on .py changes
  #   --restart                      kill old process (SIGTERM) before spawning new
  #   --stop-timeout 5s              5s grace, then SIGKILL
  #   --no-vcs-ignore                don't read .gitignore (we set our own scope)
  #   --debounce 500ms               coalesce rapid saves
  watchexec \
    --watch app \
    --exts py \
    --restart \
    --stop-timeout 5s \
    --no-vcs-ignore \
    --debounce 500ms \
    -- uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" \
    >> "${LOG_FILE}" 2>&1 &
  WATCHEXEC_PID=$!
  echo "[dev-backend] watchexec pid=${WATCHEXEC_PID}"
}

kill_watchexec() {
  echo "[dev-backend] killing watchexec tree pid=${WATCHEXEC_PID}"
  pkill -KILL -P "${WATCHEXEC_PID}" 2>/dev/null || true
  kill -KILL "${WATCHEXEC_PID}" 2>/dev/null || true
  lsof -ti tcp:"${PORT}" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  sleep 2
}

probe() {
  curl -s -o /dev/null -w "%{http_code}" --max-time "${PROBE_TIMEOUT}" \
    "${HEALTH_URL}" 2>/dev/null
}

while true; do
  start_watchexec

  # Warm-up: wait up to 90s for /healthz to come up before policing it.
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
      echo "[dev-backend] wedged → restarting watchexec tree"
      break
    fi
  done

  kill_watchexec
  echo "[dev-backend] respawning in 2s"
  sleep 2
done
