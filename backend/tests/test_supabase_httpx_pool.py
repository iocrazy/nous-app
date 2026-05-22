"""Bounded httpx pool injected into per-loop Supabase clients.

2026-05-22 incident follow-up: Supabase/Kong closed idle keep-alive
connections faster than httpcore reaped them, so server-closed sockets
piled up in CLOSE_WAIT and exhausted the ~28k ephemeral port range
("Engine Offline"). The fix injects a bounded, short-keepalive httpx
pool into every Supabase client so idle connections are closed cleanly by
httpx (before the server does) and the pool can never run away.
"""

from __future__ import annotations

import asyncio

import httpx

from app.db.supabase_client import (
    _HTTPX_LIMITS,
    _get_client_options,
)


def test_httpx_limits_are_bounded_with_short_keepalive():
    # Bounded so the pool can never grow toward the ~28k ephemeral cap.
    assert _HTTPX_LIMITS.max_connections == 50
    assert _HTTPX_LIMITS.max_keepalive_connections == 10
    # Short keepalive: httpx closes idle conns (clean FIN, auto-reaped)
    # before the server does, so they never become CLOSE_WAIT.
    assert _HTTPX_LIMITS.keepalive_expiry == 15.0


def test_get_client_options_injects_bounded_httpx_client():
    opts = _get_client_options()
    try:
        assert isinstance(opts.httpx_client, httpx.AsyncClient)
    finally:
        asyncio.run(opts.httpx_client.aclose())
