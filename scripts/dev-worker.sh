#!/usr/bin/env bash
# scripts/dev-worker.sh
#
# Dev supervisor for a DBOS worker process (no public HTTP API).
# Binds a different port than the gateway (so both can run on one
# machine) but only mounts /api/v1/healthz + /api/v1/readyz — see
# the role gate in app/main.py.
#
# Usage:
#   ./scripts/dev-worker.sh             # foreground, Ctrl-C to stop
#   nohup ./scripts/dev-worker.sh &     # background
#
# Scaling locally:
#   WORKER_PORT=8084 SUPERVISOR_TAG=dev-worker-2 \
#     BACKEND_LOG=/tmp/dev-worker-2.log ./scripts/dev-worker.sh
#   ...starts a second worker process pulling from the same DBOS queue.

export MEDIAHUB_ROLE="worker"
# Default to gateway_port + 1 so both can coexist on a dev machine
# without colliding. Override via WORKER_PORT.
export BACKEND_PORT="${WORKER_PORT:-8083}"
export SUPERVISOR_TAG="${SUPERVISOR_TAG:-dev-worker}"
export BACKEND_LOG="${BACKEND_LOG:-/tmp/${SUPERVISOR_TAG}.log}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/dev-backend.sh"
