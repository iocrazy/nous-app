"""Agent-memory stats repository function (Phase C2 observability).

Tests for:
  async get_memory_stats() -> Dict[str, Any]

in ``app.repositories.agent_memory_repository``.

Pattern: custom _Session / _Scope classes (mirrors test_agent_memory_recall.py),
patch ``read_scope`` at the repo import site. Two sequential execute calls in
one session — first for agent_memory aggregates, second for promotions.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / mock primitives
# ---------------------------------------------------------------------------

# Canned aggregate row from agent_memory table
_AM_ROW = {
    "total_active": 7,
    "vis_private": 5,
    "vis_shared": 2,
    "scope_agent_user": 4,
    "scope_team": 2,
    "scope_project": 1,
    "st_active": 7,
    "st_archived": 1,
    "st_superseded": 0,
    "created_24h": 3,
    "created_7d": 6,
    "last_created_at": "2026-06-20T10:00:00",
}

# Canned aggregate row from agent_memory_promotions table
_PROMO_ROW = {
    "pending": 1,
    "approved": 0,
    "rejected": 2,
}


class _Result:
    """Fake SQLAlchemy result supporting .mappings().first()."""

    def __init__(self, row: dict) -> None:
        self._row = row

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict:
        return self._row


class _Session:
    """Fake async session that returns AM row on first execute, promo on second."""

    def __init__(self, am_row: dict, promo_row: dict) -> None:
        self._call = 0
        self._am = am_row
        self._promo = promo_row

    async def execute(self, stmt, params=None):
        self._call += 1
        if self._call == 1:
            return _Result(self._am)
        return _Result(self._promo)


def _make_scope(am_row: dict = _AM_ROW, promo_row: dict = _PROMO_ROW):
    """Build a _Scope async context manager wrapping the given rows."""

    class _Scope:
        async def __aenter__(self):
            return _Session(am_row, promo_row)

        async def __aexit__(self, *a):
            return False

    return _Scope()


# ---------------------------------------------------------------------------
# T1: aggregate mapping correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_memory_stats_aggregates():
    """get_memory_stats maps two SQL rows into the documented dict shape."""
    from app.repositories.agent_memory_repository import get_memory_stats

    with patch(
        "app.repositories.agent_memory_repository.read_scope",
        return_value=_make_scope(),
    ):
        stats = await get_memory_stats()

    # Core totals
    assert stats["total_active"] == 7
    assert stats["created_24h"] == 3
    assert stats["created_7d"] == 6
    assert stats["last_created_at"] == "2026-06-20T10:00:00"

    # Visibility breakdown
    assert stats["by_visibility"] == {"private": 5, "shared": 2}

    # Scope breakdown
    assert stats["by_scope"] == {
        "agent_user": 4,
        "team": 2,
        "project": 1,
    }

    # Status breakdown
    assert stats["by_status"] == {
        "active": 7,
        "archived": 1,
        "superseded": 0,
    }

    # Promotions
    assert stats["promotions"] == {"pending": 1, "approved": 0, "rejected": 2}


@pytest.mark.asyncio
async def test_get_memory_stats_last_created_at_none_when_null():
    """last_created_at is None when the DB returns NULL (empty table)."""
    from app.repositories.agent_memory_repository import get_memory_stats

    am_row_null = {**_AM_ROW, "last_created_at": None}
    with patch(
        "app.repositories.agent_memory_repository.read_scope",
        return_value=_make_scope(am_row=am_row_null),
    ):
        stats = await get_memory_stats()

    assert stats["last_created_at"] is None


# ---------------------------------------------------------------------------
# T2: safe on error — never raises, returns zero-shaped dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_memory_stats_safe_on_error():
    """When read_scope raises, get_memory_stats returns the zero-shaped dict (no raise)."""
    from app.repositories.agent_memory_repository import get_memory_stats

    with patch(
        "app.repositories.agent_memory_repository.read_scope",
        side_effect=RuntimeError("db down"),
    ):
        stats = await get_memory_stats()

    # Must return the empty/zero shape, never raise
    assert stats["total_active"] == 0
    assert stats["by_visibility"] == {}
    assert stats["by_scope"] == {}
    assert stats["by_status"] == {}
    assert stats["created_24h"] == 0
    assert stats["created_7d"] == 0
    assert stats["last_created_at"] is None
    assert stats["promotions"] == {"pending": 0, "approved": 0, "rejected": 0}


@pytest.mark.asyncio
async def test_get_memory_stats_safe_on_execute_error():
    """An error during session.execute also returns zero-shaped dict."""
    from app.repositories.agent_memory_repository import get_memory_stats

    class _BrokenSession:
        async def execute(self, stmt, params=None):
            raise RuntimeError("query error")

    class _Scope:
        async def __aenter__(self):
            return _BrokenSession()

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.agent_memory_repository.read_scope",
        return_value=_Scope(),
    ):
        stats = await get_memory_stats()

    assert stats["total_active"] == 0
    assert stats["promotions"] == {"pending": 0, "approved": 0, "rejected": 0}
