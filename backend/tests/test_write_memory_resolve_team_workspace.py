"""``write_memory._resolve_team_workspace`` — conversations-only lookup.

Conversations Phase 3, Task 6 collapsed the compatibility layer: the
legacy ``ai_sessions`` fallback this function used to try after a
conversations miss is gone (the legacy table itself is dropped in Wave 2).

The conversations column is ``scope_id`` (aliased ``AS team_id`` in the
SELECT), NOT a literal ``team_id`` column — that was the ai_sessions-only
column name.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolve_team_workspace_reads_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        return {"team_id": 900000000000001}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    workspace = await write_memory._resolve_team_workspace("888")

    assert workspace == "team-900000000000001"
    assert len(calls) == 1
    assert "public.conversations" in calls[0]["sql"]
    assert "scope_id AS team_id" in calls[0]["sql"]
    assert calls[0]["params"] == {"sid": 888}


@pytest.mark.asyncio
async def test_resolve_team_workspace_returns_none_when_no_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    async def fake_fetch_one(sql: str, params=None):
        return {"team_id": None}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    assert await write_memory._resolve_team_workspace("888") is None


@pytest.mark.asyncio
async def test_resolve_team_workspace_returns_none_when_session_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    async def fake_fetch_one(sql: str, params=None):
        return None

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    assert await write_memory._resolve_team_workspace("888") is None


@pytest.mark.asyncio
async def test_resolve_team_workspace_degrades_to_none_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any failure (bad session_id, DB outage) must degrade to None
    (deployment default workspace) — never raise, never block the write."""
    from app.workflows import write_memory

    assert await write_memory._resolve_team_workspace("not-an-int") is None
