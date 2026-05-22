"""Regression: run_async drains the transient loop's Supabase client.

2026-05-22 prod incident
------------------------
Each ``run_async()`` spins up a *fresh* event loop, and
``AsyncSupabaseClient`` caches one httpx-backed client per loop. The
throwaway loop's client was GC'd without ``aclose()`` — so its open
sockets to Supabase/Kong leaked. Over ~2 days the gateway exhausted the
ephemeral port range (32768-60999) and every new Supabase REST call
failed with ``[Errno 99] Cannot assign requested address`` (httpx "All
connection attempts failed"); the Engine status badge flipped Offline.

The fix: ``run_async`` wraps the coroutine so the loop closes its
per-loop Supabase clients in a ``finally`` before being torn down.
These tests pin that behaviour for both run_async code paths.
"""

from __future__ import annotations

import pytest

from app.db import supabase_client as sc_mod
from app.db.supabase_client import AsyncSupabaseClient
from app.tasks.utils import run_async


class _Sub:
    def __init__(self) -> None:
        self.aclose_called = False

    async def aclose(self) -> None:
        self.aclose_called = True


class _FakeClient:
    """Stand-in for a supabase AsyncClient with closeable sub-clients."""

    def __init__(self) -> None:
        self.postgrest = _Sub()
        self.storage = _Sub()
        self.auth = _Sub()


@pytest.fixture(autouse=True)
def _clear_caches_and_settings(monkeypatch):
    AsyncSupabaseClient._instances.clear()
    AsyncSupabaseClient._admin_instances.clear()
    monkeypatch.setattr(sc_mod.settings, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(
        sc_mod.settings, "SUPABASE_SERVICE_ROLE_KEY", "k", raising=False
    )
    yield
    AsyncSupabaseClient._instances.clear()
    AsyncSupabaseClient._admin_instances.clear()


def test_run_async_closes_transient_loop_client_no_running_loop(monkeypatch):
    """Primary path (sync caller, e.g. a DBOS sync step): run_async uses
    asyncio.run on a fresh loop. The client acquired inside must be closed
    and its cache entry evicted before run_async returns."""
    created: list[_FakeClient] = []

    async def fake_create(*args, **kwargs):
        c = _FakeClient()
        created.append(c)
        return c

    monkeypatch.setattr(sc_mod, "create_async_client", fake_create)

    async def work() -> str:
        await AsyncSupabaseClient.get_admin_client()
        return "ok"

    assert run_async(work()) == "ok"
    assert len(created) == 1
    # Sockets drained (not merely GC'd).
    assert created[0].postgrest.aclose_called is True
    assert created[0].storage.aclose_called is True
    # No leaked cache entry.
    assert len(AsyncSupabaseClient._admin_instances) == 0


async def test_run_async_closes_transient_loop_client_from_running_loop(monkeypatch):
    """Nested path (run_async called while a loop is already running, e.g.
    a sync DBOS step inheriting the workflow loop): it bounces to a worker
    thread whose fresh loop must likewise drain its Supabase client."""
    created: list[_FakeClient] = []

    async def fake_create(*args, **kwargs):
        c = _FakeClient()
        created.append(c)
        return c

    monkeypatch.setattr(sc_mod, "create_async_client", fake_create)

    async def work() -> str:
        await AsyncSupabaseClient.get_admin_client()
        return "ok"

    # We are inside pytest-asyncio's running loop → worker-thread path.
    assert run_async(work()) == "ok"
    assert len(created) == 1
    assert created[0].postgrest.aclose_called is True
    assert len(AsyncSupabaseClient._admin_instances) == 0
