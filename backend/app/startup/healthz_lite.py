"""Dedicated HTTP server on its own daemon thread that answers /healthz/lite
with HTTP 200 — independently of the main FastAPI event loop.

Why a separate server: docker-compose's healthcheck previously curled the
main app's /api/v1/healthz. When the asyncio event loop blocked (e.g.,
DBOS recovery storm 2026-05-27), the healthcheck timed out and autoheal
restarted the container — but the restart didn't fix anything because the
blocker re-occurred on every launch. A healthcheck that shares the loop
it judges cannot distinguish "loop is blocked" from "loop is slow this
second" and there's no way to make it more reliable without also making
it lying.

A separate thread bound to a dedicated port reports the truth: if THIS
server can answer, the process is alive enough to fork a thread but the
main loop may still be in trouble — making the signal a strict
liveness probe rather than a "are you serving FastAPI" probe.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from loguru import logger

_DEFAULT_PORT = 8090
_PATH = "/healthz/lite"


class _Handler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — always 200, no logging spam."""

    def do_GET(self) -> None:  # noqa: N802 — stdlib API
        if self.path != _PATH:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
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
