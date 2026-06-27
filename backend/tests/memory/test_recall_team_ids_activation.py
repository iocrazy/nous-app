"""Task C2 — activate shared recall by populating team_ids from team_members.

Tests:
  1. get_user_team_ids returns ids from DB
  2. get_user_team_ids returns [] on error (never raises)
  3. _safe_recall_agent_memory populates team_ids when FEATURE_AGENT_MEMORY=True
  4. _safe_recall_agent_memory does NOT fetch team ids when flag is False (cost gated)
  5. _safe_recall_agent_memory degrades to team_ids=() when team fetch fails
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.agent_memory_repository import get_user_team_ids
from app.services.ai.chat.ai_library_chat_wiring import _safe_recall_agent_memory
from app.services.ai.memory.agent_memory import MemoryContext

# ---------------------------------------------------------------------------
# get_user_team_ids
# ---------------------------------------------------------------------------


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _TeamIdsSession:
    def __init__(self, rows, *, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute
        self.captured_params = {}

    async def execute(self, stmt, params=None):
        if self._raise:
            raise RuntimeError("db down")
        self.captured_params = params or {}
        return self

    def scalars(self):
        return _ScalarsResult(self._rows)


class _TeamIdsScope:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_get_user_team_ids_returns_ids():
    session = _TeamIdsSession([10, 20])
    scope = _TeamIdsScope(session)
    with patch(
        "app.repositories.agent_memory_repository.read_scope", return_value=scope
    ):
        result = await get_user_team_ids("u1")

    assert result == [10, 20]
    assert session.captured_params.get("uid") == "u1"


@pytest.mark.asyncio
async def test_get_user_team_ids_empty_on_error():
    session = _TeamIdsSession([], raise_on_execute=True)
    scope = _TeamIdsScope(session)
    with patch(
        "app.repositories.agent_memory_repository.read_scope", return_value=scope
    ):
        result = await get_user_team_ids("u1")

    assert result == []


# ---------------------------------------------------------------------------
# _safe_recall_agent_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_populates_team_ids_when_flag_on(monkeypatch):
    """When FEATURE_AGENT_MEMORY=True, team_ids should be fetched and ctx rebuilt."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_AGENT_MEMORY", True)

    ctx = MemoryContext(user_id="u1", team_ids=())

    captured_ctx = {}

    async def _fake_recall(inner_ctx, query, *, limit=5):
        captured_ctx["ctx"] = inner_ctx
        return []

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.get_user_team_ids",
            new=AsyncMock(return_value=[7]),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.recall",
            new=_fake_recall,
        ),
    ):
        result = await _safe_recall_agent_memory(ctx, "deploy backend")

    assert result == []
    assert captured_ctx["ctx"].team_ids == (7,)


@pytest.mark.asyncio
async def test_recall_skips_team_fetch_when_flag_off(monkeypatch):
    """When FEATURE_AGENT_MEMORY=False, team fetch must NOT be called (cost gated)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_AGENT_MEMORY", False)

    ctx = MemoryContext(user_id="u1", team_ids=())
    mock_get_team_ids = AsyncMock(return_value=[7])

    with patch(
        "app.services.ai.chat.ai_library_chat_wiring.get_user_team_ids",
        new=mock_get_team_ids,
    ):
        result = await _safe_recall_agent_memory(ctx, "anything")

    assert result == []
    mock_get_team_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_degrades_when_team_fetch_fails(monkeypatch):
    """If get_user_team_ids raises, recall still runs with team_ids=() and does not raise."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_AGENT_MEMORY", True)

    ctx = MemoryContext(user_id="u1", team_ids=())

    captured_ctx = {}

    async def _fake_recall(inner_ctx, query, *, limit=5):
        captured_ctx["ctx"] = inner_ctx
        return []

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.get_user_team_ids",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.recall",
            new=_fake_recall,
        ),
    ):
        result = await _safe_recall_agent_memory(ctx, "anything")

    # Should not raise, should degrade gracefully
    assert result == []
    assert captured_ctx["ctx"].team_ids == ()
