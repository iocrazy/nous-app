#!/usr/bin/env bash
# Start Xvfb, then the API. Xvfb is started explicitly rather than via
# `xvfb-run` so the display number is fixed and /healthz can verify the X socket
# by path instead of guessing.
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
SCREEN="${XVFB_SCREEN:-1920x1080x24}"

Xvfb "$DISPLAY" -screen 0 "$SCREEN" -nolisten tcp &
XVFB_PID=$!
trap 'kill "$XVFB_PID" 2>/dev/null || true' EXIT INT TERM

# Bounded wait for the socket - never an unbounded poll (design doc 7.2).
SOCKET="/tmp/.X11-unix/X${DISPLAY#:}"
for _ in $(seq 1 100); do
    [ -e "$SOCKET" ] && break
    sleep 0.1
done

if [ ! -e "$SOCKET" ]; then
    # Do not fail hard: the API must come up so /healthz can report
    # xvfb=false. A container that exits here just looks like a crash loop.
    echo "warning: Xvfb socket $SOCKET did not appear within 10s" >&2
fi

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${BROWSER_PORT:-8090}" \
    --no-access-log
