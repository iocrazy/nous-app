"""Task 6 / S8: ``write_memory._resolve_team_workspace`` conversations-first,
ai_sessions-fallback lookup (parity checklist §3.6/§3.8).

Snowflake ids are minted exactly once by ``generate_snowflake_id()``, so a
given ``session_id`` can never exist as a row in BOTH ``conversations`` and
``ai_sessions`` — the conversations query is tried first and short-circuits
on a hit; a miss falls back to the legacy ai_sessions lookup.

The conversations column is ``scope_id`` (aliased ``AS team_id`` in the
SELECT), NOT a literal ``team_id`` column — that's the ai_sessions-only
column name (checklist risk #3).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolve_team_workspace_tries_conversations_first(
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
async def test_resolve_team_workspace_falls_back_to_ai_sessions_when_no_conversations_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        if "public.conversations" in sql:
            return None
        return {"team_id": 42}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    workspace = await write_memory._resolve_team_workspace("888")

    assert workspace == "team-42"
    assert len(calls) == 2
    assert "public.conversations" in calls[0]["sql"]
    assert "public.ai_sessions" in calls[1]["sql"]
    assert "team_id" in calls[1]["sql"]
    assert calls[1]["params"] == {"sid": 888}


@pytest.mark.asyncio
async def test_resolve_team_workspace_returns_none_when_neither_table_has_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    async def fake_fetch_one(sql: str, params=None):
        if "public.conversations" in sql:
            return None
        return {"team_id": None}

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
