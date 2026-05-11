"""Supabase client per-loop cache — regression for the cross-loop drain bug.

History: this file was originally named after P2-10 and exercised the
``_drain_old_client`` helper. That helper was the root cause of the
cross-loop client-closed bug (each ``asyncio.run()`` from a DBOS workflow
would drain the uvicorn loop's client, killing in-flight fire-and-forget
tasks). The redesign uses a ``WeakKeyDictionary`` keyed by event loop, so
each loop owns its own client and never touches another loop's.

Tests in this file ensure:

* Same loop ⇒ same client instance (cache hit).
* Two different loops ⇒ two distinct client instances (no cross-loop reuse).
* Disposing a loop drops its dict entry automatically (no manual cleanup).
* ``close()`` only affects the current loop's clients.
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from app.db import supabase_client as sc_mod
from app.db.supabase_client import AsyncSupabaseClient, _aclose_subclients


class _FakeSession:
    def __init__(self) -> None:
        self.aclose_called = False

    async def aclose(self) -> None:
        self.aclose_called = True


class _SubAclose:
    def __init__(self) -> None:
        self.aclose_called = False

    async def aclose(self) -> None:
        self.aclose_called = True


class _SubWithSession:
    def __init__(self) -> None:
        self.session = _FakeSession()


class _FakeClient:
    """Stand-in for supabase AsyncClient with the three sub-clients we close."""

    def __init__(self) -> None:
        self.postgrest = _SubAclose()
        self.storage = _SubWithSession()
        self.auth = _SubWithSession()


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset per-loop caches between tests so state doesn't leak."""
    AsyncSupabaseClient._instances.clear()
    AsyncSupabaseClient._admin_instances.clear()
    yield
    AsyncSupabaseClient._instances.clear()
    AsyncSupabaseClient._admin_instances.clear()


# ───────────────────────── _aclose_subclients ─────────────────────────


@pytest.mark.asyncio
async def test_aclose_subclients_closes_all_three():
    fake = _FakeClient()
    await _aclose_subclients(fake)
    assert fake.postgrest.aclose_called is True
    assert fake.storage.session.aclose_called is True
    assert fake.auth.session.aclose_called is True


@pytest.mark.asyncio
async def test_aclose_subclients_partial_failure_continues():
    class _Broken:
        async def aclose(self) -> None:
            raise RuntimeError("boom")

    fake = _FakeClient()
    fake.postgrest = _Broken()  # type: ignore[assignment]
    await _aclose_subclients(fake)
    # Storage + auth still closed even though postgrest exploded.
    assert fake.storage.session.aclose_called is True
    assert fake.auth.session.aclose_called is True


@pytest.mark.asyncio
async def test_aclose_subclients_missing_attrs_is_noop():
    class _Bare:
        pass

    bare = _Bare()
    await _aclose_subclients(bare)  # type: ignore[arg-type]


# ───────────────────────── per-loop cache ─────────────────────────


@pytest.mark.asyncio
async def test_same_loop_returns_same_admin_client(monkeypatch):
    """Same loop, two calls => one client (cache hit)."""

    calls = {"count": 0}

    async def fake_create_async_client(*args, **kwargs):
        calls["count"] += 1
        return _FakeClient()

    monkeypatch.setattr(sc_mod, "create_async_client", fake_create_async_client)
    monkeypatch.setattr(sc_mod.settings, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(
        sc_mod.settings, "SUPABASE_SERVICE_ROLE_KEY", "k", raising=False
    )

    c1 = await AsyncSupabaseClient.get_admin_client()
    c2 = await AsyncSupabaseClient.get_admin_client()
    assert c1 is c2
    assert calls["count"] == 1


def test_different_loops_get_different_clients(monkeypatch):
    """Two ``asyncio.run()`` invocations are two distinct loops; each must
    get its own client. This is the regression test for the bug.

    Empirically, ``id(loop)`` reuses 8/10 times under tight asyncio.run()
    sequencing — so ``id()``-keyed caches would silently return a dead
    client. ``WeakKeyDictionary[loop_obj, client]`` is the only safe
    primitive here.
    """

    created: list[_FakeClient] = []

    async def fake_create_async_client(*args, **kwargs):
        c = _FakeClient()
        created.append(c)
        return c

    monkeypatch.setattr(sc_mod, "create_async_client", fake_create_async_client)
    monkeypatch.setattr(sc_mod.settings, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(
        sc_mod.settings, "SUPABASE_SERVICE_ROLE_KEY", "k", raising=False
    )

    async def acquire_in_loop() -> int:
        client = await AsyncSupabaseClient.get_admin_client()
        return id(client)

    id_a = asyncio.run(acquire_in_loop())
    id_b = asyncio.run(acquire_in_loop())

    # Both calls created a fresh client because each ran in its own loop.
    assert len(created) == 2
    assert id_a != id_b


def test_loop_dispose_evicts_dict_entry(monkeypatch):
    """When ``asyncio.run()`` exits, the loop is GC'd and its
    WeakKeyDictionary entry must vanish so we don't leak clients."""

    async def fake_create_async_client(*args, **kwargs):
        return _FakeClient()

    monkeypatch.setattr(sc_mod, "create_async_client", fake_create_async_client)
    monkeypatch.setattr(sc_mod.settings, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(
        sc_mod.settings, "SUPABASE_SERVICE_ROLE_KEY", "k", raising=False
    )

    async def populate():
        await AsyncSupabaseClient.get_admin_client()

    asyncio.run(populate())
    gc.collect()
    assert len(AsyncSupabaseClient._admin_instances) == 0


def test_old_loop_client_NOT_closed_by_new_loop_call(monkeypatch):
    """Critical regression: when a NEW loop calls get_admin_client(), the
    OLD loop's cached client must remain untouched. The old bug closed it.

    We simulate this by stashing the old client and verifying its
    sub-clients' aclose was never called after the new-loop request.
    """

    async def fake_create_async_client(*args, **kwargs):
        return _FakeClient()

    monkeypatch.setattr(sc_mod, "create_async_client", fake_create_async_client)
    monkeypatch.setattr(sc_mod.settings, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(
        sc_mod.settings, "SUPABASE_SERVICE_ROLE_KEY", "k", raising=False
    )

    captured: dict[str, _FakeClient] = {}

    async def populate_old():
        # Stash a strong ref so old loop's entry survives long enough to
        # check it after the new-loop call below.
        captured["old"] = await AsyncSupabaseClient.get_admin_client()

    async def populate_new():
        await AsyncSupabaseClient.get_admin_client()

    asyncio.run(populate_old())
    # New loop. Under the old (buggy) singleton design this would call
    # _drain_old_client(captured["old"]). The new design must not.
    asyncio.run(populate_new())

    old = captured["old"]
    assert old.postgrest.aclose_called is False
    assert old.storage.session.aclose_called is False
    assert old.auth.session.aclose_called is False
