"""Keep-alive-disabled httpx pool injected into per-loop Supabase clients.

2026-05-22 incident follow-up: Kong closed idle keep-alive connections but
httpcore left the server-closed sockets in CLOSE_WAIT (known httpcore bug),
so they piled up and exhausted the ~28k ephemeral port range ("Engine
Offline"). A short-keepalive pool did NOT help (even a small idle pool
leaks). The fix disables keep-alive entirely so no idle connection is ever
retained — httpx closes each connection itself after the response, and
CLOSE_WAIT can't form.
"""

from __future__ import annotations

import asyncio

import httpx

from app.db.supabase_client import (
    _HTTPX_LIMITS,
    _get_client_options,
)


def test_httpx_keepalive_disabled():
    # Bounded total so the pool can never grow toward the ~28k ephemeral cap.
    assert _HTTPX_LIMITS.max_connections == 50
    # Keep-alive DISABLED: no idle connections are retained, so the server
    # never gets an idle connection to close — httpx closes each itself
    # (client FIN → TIME_WAIT, auto-reaped), so CLOSE_WAIT can't accumulate.
    assert _HTTPX_LIMITS.max_keepalive_connections == 0
    assert _HTTPX_LIMITS.keepalive_expiry == 0.0


def test_get_client_options_injects_bounded_httpx_client():
    opts = _get_client_options()
    try:
        assert isinstance(opts.httpx_client, httpx.AsyncClient)
    finally:
        asyncio.run(opts.httpx_client.aclose())
