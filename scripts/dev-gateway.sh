#!/usr/bin/env bash
# scripts/dev-gateway.sh
#
# Dev supervisor for the FastAPI gateway process (no DBOS workers).
# Pair with scripts/dev-worker.sh to test the PR-D8 Phase 1 split-mode
# locally.
#
# Why split into two scripts:
#   - Gateway and worker have different failure modes; running them under
#     the same supervisor would conflate restart triggers (a wedged worker
#     would restart the gateway, dropping in-flight HTTP).
#   - Different log files keeps tails grep-able by role.
#   - Worker doesn't need a published HTTP port for normal use, so its
#     supervisor probes a different bind address.
#
# Combined-mode dev (single process for both) still works via the
# original scripts/dev-backend.sh — keep that as the lightweight default.

export MEDIAHUB_ROLE="gateway"
export BACKEND_PORT="${BACKEND_PORT:-8082}"
export SUPERVISOR_TAG="dev-gateway"
export BACKEND_LOG="${BACKEND_LOG:-/tmp/dev-gateway.log}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/dev-backend.sh"
