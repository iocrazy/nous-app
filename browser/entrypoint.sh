#!/usr/bin/env bash
# Start Xvfb, then the API. Xvfb is started explicitly rather than via
# `xvfb-run` so the display number is fixed and /healthz can verify it.
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
SCREEN="${XVFB_SCREEN:-1920x1080x24}"
DISPLAY_NUM="${DISPLAY#:}"
SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM}"
LOCK="/tmp/.X${DISPLAY_NUM}-lock"

# Clear the previous run's leftovers before starting.
#
# Xvfb refuses to start on a display whose lock file exists — "Server is
# already active for display 99. If this server is no longer running, remove
# /tmp/.X99-lock". After an unclean exit that lock is a lie, and the container
# then runs with no X server at all: the API comes up, every headed launch
# fails, and the failure surfaces to the user as "the platform rejected this
# login" (2026-08-06).
#
# Deleting them is safe *here* specifically because this is the container's
# entrypoint: one X server per container, and nothing else can be using
# display "$DISPLAY_NUM" at the moment we run.
rm -f "$LOCK" "$SOCKET"

start_xvfb() {
    Xvfb "$DISPLAY" -screen 0 "$SCREEN" -nolisten tcp &
    XVFB_PID=$!
}

# Connect to the socket instead of stat-ing it: the file reappears the instant
# Xvfb creates it, but a display that is not accepting connections yet (or ever)
# would still pass an existence test. Same reason app/browser_runtime.py's
# xvfb_ready() dials the socket.
x_accepts_connections() {
    python3 - "$SOCKET" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(1.0)
try:
    s.connect(sys.argv[1])
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

start_xvfb
trap 'kill "$XVFB_PID" 2>/dev/null || true' EXIT INT TERM

# Bounded wait — never an unbounded poll (design doc 7.2).
for _ in $(seq 1 100); do
    x_accepts_connections && break
    sleep 0.1
done

if ! x_accepts_connections; then
    # Deliberately not fatal: the API must come up so /healthz can report
    # xvfb=false with a body someone can read. A container that exits during
    # startup just looks like a crash loop and hides which of the two things
    # is broken.
    echo "warning: no X server accepting connections on $DISPLAY after 10s" >&2
fi

# Supervise: if Xvfb dies *later*, take the container down with it.
#
# The startup case above and this one call for opposite handling. A display
# that never came up is usually a configuration problem, and restarting cannot
# fix it. A display that came up and then died is usually transient (OOM, a
# crashed renderer taking it along), and a restart genuinely recovers — the
# lock cleanup above makes the next boot clean. Staying alive with a dead X
# server is the worst option: the service answers, accepts publish jobs, and
# fails every one of them.
#
# compose sets `restart: unless-stopped`, so terminating PID 1 is how we ask
# for that restart.
(
    while kill -0 "$XVFB_PID" 2>/dev/null; do
        sleep 5
    done
    echo "error: Xvfb (pid $XVFB_PID) exited — stopping so the container restarts clean" >&2
    kill -TERM 1 2>/dev/null || true
) &

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${BROWSER_PORT:-8090}" \
    --no-access-log
