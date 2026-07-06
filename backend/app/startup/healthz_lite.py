"""Dedicated HTTP server on its own daemon thread that answers /healthz/lite
— independently of the main FastAPI event loop.

Why a separate server: docker-compose's healthcheck previously curled the
main app's /api/v1/healthz. When the asyncio event loop blocked (e.g.,
DBOS recovery storm 2026-05-27), the healthcheck timed out and autoheal
restarted the container — but the restart didn't fix anything because the
blocker re-occurred on every launch. A healthcheck that shares the loop
it judges cannot distinguish "loop is blocked" from "loop is slow this
second" and there's no way to make it more reliable without also making
it lying.

A separate thread bound to a dedicated port reports the truth: if THIS
server can answer, the process is alive enough to fork a thread.

Loop-freeze detection (2026-07-06): pure process-liveness turned out to
be its own blind spot — the prod event loop froze solid for 2 hours (all
HTTP hung, zero log output) while this sidecar kept answering 200 and
autoheal never fired. The fix is a heartbeat: the main loop bumps
``beat()`` every few seconds; once the loop has proven alive at least
once, a heartbeat older than ``HEALTHZ_LOOP_STALE_S`` (default 120s)
flips this endpoint to 503 → docker marks the container unhealthy →
autoheal SIGKILLs it → restart policy brings it back.

The 2026-05-27 restart-storm concern is preserved by the "proven alive
at least once" rule: a loop that never manages a first beat (startup
recovery storm) keeps the endpoint at 200 — exactly the old behavior —
so a boot-time blocker cannot trigger a kill loop. Only a RUNNING loop
that stops beating is judged dead, and that is precisely the state a
restart fixes.
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from loguru import logger

_DEFAULT_PORT = 8090
_PATH = "/healthz/lite"

# Loop-heartbeat freshness window (seconds). 0 disables the staleness
# check entirely (pure process-liveness, pre-2026-07-06 behavior).
# 120s default: an order of magnitude above any "loop is slow this
# second" blip, far below the 2-hour outage this exists to catch.
_STALE_S = float(os.getenv("HEALTHZ_LOOP_STALE_S", "120") or 0)

# Monotonic timestamp of the main loop's last beat; None until the loop
# proves alive once. Single-writer (the loop's heartbeat task) + GIL make
# the bare float assignment safe to read from the sidecar thread.
_last_beat: Optional[float] = None


def beat() -> None:
    """Record one main-loop heartbeat. Called from an asyncio task on the
    main loop — if the loop freezes, this stops being called, and that is
    the whole signal."""
    global _last_beat
    _last_beat = time.monotonic()


def reset() -> None:
    """Forget the heartbeat (tests + fresh lifespans)."""
    global _last_beat
    _last_beat = None


def _loop_stale() -> Optional[float]:
    """Return the heartbeat's age in seconds when it is stale, else None.

    None also covers: staleness check disabled, or loop never beat yet
    (startup grace — see module docstring).
    """
    if _STALE_S <= 0 or _last_beat is None:
        return None
    age = time.monotonic() - _last_beat
    return age if age > _STALE_S else None


class _Handler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — no logging spam."""

    def do_GET(self) -> None:  # noqa: N802 — stdlib API
        if self.path != _PATH:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        stale_age = _loop_stale()
        if stale_age is not None:
            body = f"loop-stale {stale_age:.0f}s\n".encode()
            self.send_response(503)
        else:
            body = b"ok\n"
            self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — stdlib API
        # Suppress per-request stdout (docker would log every 30s healthcheck).
        return


def start(port: Optional[int] = None) -> HTTPServer:
    """Start the lite server on a daemon thread and return the server handle.

    port=0 lets the OS pick a free port (used by tests).
    """
    bind_port = _DEFAULT_PORT if port is None else port
    server = HTTPServer(("0.0.0.0", bind_port), _Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="healthz-lite",
        daemon=True,
    )
    thread.start()
    logger.info(f"[healthz-lite] listening on 0.0.0.0:{server.server_port}{_PATH}")
    return server


def stop(server: HTTPServer) -> None:
    """Shut the server down + close its socket."""
    try:
        server.shutdown()
        server.server_close()
    except Exception as exc:
        logger.warning(f"[healthz-lite] shutdown raised {exc!r}")
