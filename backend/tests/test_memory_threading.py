"""M1 — episodic memory threading: assign + load."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.services.ai.memory.threading import (
    DEFAULT_THREAD_WINDOW_MINUTES,
    assign_thread_for,
    load_thread_for_memory,
)

_NOW = datetime(2026, 5, 3, 12, 0, 0)


@pytest.mark.asyncio
async def test_no_session_returns_none():
    """Cross-session memory (active_remember from CLI etc) → no thread."""

    async def _fetch(agent_id, user_id, session_id, limit):
        return []

    result = await assign_thread_for(
        agent_id="a",
        user_id="u",
        session_id=None,
        created_at=_NOW,
        fetch_recent_in_session=_fetch,
    )
    assert result is None


@pytest.mark.asyncio
async def test_first_memory_in_session_mints_new_thread():
    """No previous memories in session → fresh UUID thread."""

    async def _fetch(*_):
        return []

    result = await assign_thread_for(
        agent_id="a",
        user_id="u",
        session_id="s",
        created_at=_NOW,
        fetch_recent_in_session=_fetch,
    )
    assert isinstance(result, UUID)


@pytest.mark.asyncio
async def test_recent_thread_reused_when_within_window():
    """Last memory was 5 min ago → same thread."""
    existing_thread = uuid4()

    async def _fetch(*_):
        return [
            {
                "thread_id": str(existing_thread),
                "created_at": (_NOW - timedelta(minutes=5)).isoformat(),
            }
        ]

    result = await assign_thread_for(
        agent_id="a",
        user_id="u",
        session_id="s",
        created_at=_NOW,
        fetch_recent_in_session=_fetch,
    )
    assert result == existing_thread


@pytest.mark.asyncio
async def test_old_thread_not_reused():
    """Last memory was 60 min ago (> 30 min window) → new thread."""
    old_thread = uuid4()

    async def _fetch(*_):
        return [
            {
                "thread_id": str(old_thread),
                "created_at": (_NOW - timedelta(minutes=60)).isoformat(),
            }
        ]

    result = await assign_thread_for(
        agent_id="a",
        user_id="u",
        session_id="s",
        created_at=_NOW,
        fetch_recent_in_session=_fetch,
    )
    assert isinstance(result, UUID)
    assert result != old_thread


@pytest.mark.asyncio
async def test_skips_rows_without_thread_id():
    """Rows in same session but no thread_id (legacy / un-classified)
    are skipped; loop continues looking for one with thread_id."""
    target_thread = uuid4()

    async def _fetch(*_):
        return [
            {
                "thread_id": None,
                "created_at": (_NOW - timedelta(minutes=2)).isoformat(),
            },
            {
                "thread_id": str(target_thread),
                "created_at": (_NOW - timedelta(minutes=10)).isoformat(),
            },
        ]

    result = await assign_thread_for(
        agent_id="a",
        user_id="u",
        session_id="s",
        created_at=_NOW,
        fetch_recent_in_session=_fetch,
    )
    assert result == target_thread


@pytest.mark.asyncio
async def test_fetch_failure_returns_uuid_does_not_block():
    """DB error in fetch → mint new thread, don't block writer."""

    async def _broken(*_):
        raise RuntimeError("DB down")

    result = await assign_thread_for(
        agent_id="a",
        user_id="u",
        session_id="s",
        created_at=_NOW,
        fetch_recent_in_session=_broken,
    )
    assert isinstance(result, UUID)


@pytest.mark.asyncio
async def test_window_param_overrides_default():
    """Custom window_minutes — confirm it's actually used."""
    fresh_thread = uuid4()

    async def _fetch(*_):
        return [
            {
                "thread_id": str(fresh_thread),
                "created_at": (_NOW - timedelta(minutes=8)).isoformat(),
            },
        ]

    # window=5 → 8min ago is OUTSIDE window → new thread
    result = await assign_thread_for(
        agent_id="a",
        user_id="u",
        session_id="s",
        created_at=_NOW,
        fetch_recent_in_session=_fetch,
        window_minutes=5,
    )
    assert result != fresh_thread


@pytest.mark.unit
def test_default_window_documented():
    """Sanity: 30 min — covers a sustained discussion."""
    assert 10 <= DEFAULT_THREAD_WINDOW_MINUTES <= 120


# ─── load_thread_for_memory ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_thread_returns_siblings():
    siblings = [
        {"id": "m1", "summary": "first", "created_at": "2026-05-03T12:00:00"},
        {"id": "m2", "summary": "second", "created_at": "2026-05-03T12:05:00"},
        {"id": "m3", "summary": "third", "created_at": "2026-05-03T12:10:00"},
    ]

    async def _fetch(_mid):
        return siblings

    result = await load_thread_for_memory(memory_id="m2", fetch_thread=_fetch)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_load_thread_caps_at_max_siblings():
    rows = [{"id": f"m{i}"} for i in range(20)]

    async def _fetch(_mid):
        return rows

    result = await load_thread_for_memory(
        memory_id="m0", fetch_thread=_fetch, max_siblings=5
    )
    assert len(result) == 6  # max_siblings + 1


@pytest.mark.asyncio
async def test_load_thread_swallows_errors():
    async def _broken(_mid):
        raise RuntimeError("nope")

    result = await load_thread_for_memory(memory_id="m1", fetch_thread=_broken)
    assert result == []
