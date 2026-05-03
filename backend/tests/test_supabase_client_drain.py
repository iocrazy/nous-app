"""P2-10 — Supabase client drain on loop swap (issue #21 mitigation)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.db.supabase_client import AsyncSupabaseClient


class _FakeSession:
    """Stand-in for httpx.AsyncClient inside a sub-client."""

    def __init__(self) -> None:
        self.aclose_called = False
        self.aclose_count = 0

    async def aclose(self) -> None:
        self.aclose_called = True
        self.aclose_count += 1


class _SubClientWithAclose:
    """Stand-in for postgrest — exposes aclose directly."""

    def __init__(self) -> None:
        self.aclose_called = False

    async def aclose(self) -> None:
        self.aclose_called = True


class _SubClientWithSession:
    """Stand-in for storage/auth — has .session.aclose."""

    def __init__(self) -> None:
        self.session = _FakeSession()


def _fake_supabase_client() -> Any:
    """Build a stand-in with the three sub-client attributes the drain
    hook tries to close."""
    return SimpleNamespace(
        postgrest=_SubClientWithAclose(),
        storage=_SubClientWithSession(),
        auth=_SubClientWithSession(),
    )


@pytest.mark.asyncio
async def test_drain_closes_all_subclients():
    fake = _fake_supabase_client()
    await AsyncSupabaseClient._drain_old_client(fake)
    assert fake.postgrest.aclose_called is True
    assert fake.storage.session.aclose_called is True
    assert fake.auth.session.aclose_called is True


@pytest.mark.asyncio
async def test_drain_handles_none():
    """Defensive: drain(None) is a no-op, never raises."""
    await AsyncSupabaseClient._drain_old_client(None)


@pytest.mark.asyncio
async def test_drain_continues_on_partial_failure():
    """One sub-client raising during aclose() should NOT prevent the
    others from being drained — full leak prevention requires all three."""

    class _BrokenSub:
        async def aclose(self) -> None:
            raise RuntimeError("boom")

    fake = SimpleNamespace(
        postgrest=_BrokenSub(),
        storage=_SubClientWithSession(),
        auth=_SubClientWithSession(),
    )
    await AsyncSupabaseClient._drain_old_client(fake)
    # storage + auth still drained even though postgrest exploded
    assert fake.storage.session.aclose_called is True
    assert fake.auth.session.aclose_called is True


@pytest.mark.asyncio
async def test_drain_handles_missing_attrs():
    """If a sub-client has neither .aclose nor .session.aclose, skip
    silently — corresponds to a future Supabase SDK that renames things."""
    fake = SimpleNamespace(
        postgrest=SimpleNamespace(),  # nothing closeable
        storage=SimpleNamespace(),
        auth=SimpleNamespace(),
    )
    await AsyncSupabaseClient._drain_old_client(fake)
    # No exception, no false positives. Test passes if we get here.
