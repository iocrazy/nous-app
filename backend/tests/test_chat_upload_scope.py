"""``chat_upload._get_session_team_id`` — conversations-only lookup.

Conversations Phase 3, Task 6 collapsed the compatibility layer: the
legacy ``ai_sessions`` fallback this used to try after a conversations
miss is gone (the legacy table itself is dropped in Wave 2).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_session_team_id_reads_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.library import chat_upload as m

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        return {"team_id": 900000000000001}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    team_id = await m._get_session_team_id("888")

    assert team_id == 900000000000001
    assert len(calls) == 1
    assert "public.conversations" in calls[0]["sql"]
    assert "scope_id AS team_id" in calls[0]["sql"]
    assert calls[0]["params"] == {"id": 888}


@pytest.mark.asyncio
async def test_get_session_team_id_returns_none_when_session_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.library import chat_upload as m

    async def fake_fetch_one(sql: str, params=None):
        return None

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    assert await m._get_session_team_id("888") is None


@pytest.mark.asyncio
async def test_get_session_team_id_returns_none_when_null_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A found conversations row with a NULL scope_id (shouldn't happen for
    a direct_agent session, but defensive) returns None."""
    from app.services.library import chat_upload as m

    async def fake_fetch_one(sql: str, params=None):
        return {"team_id": None}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    assert await m._get_session_team_id("888") is None


@pytest.mark.asyncio
async def test_get_session_team_id_conversations_row_honored_by_resolve_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a session_id resolves to ("team", str(team_id)) via
    resolve_chat_scope (a personal conversation's scope_id IS a team_id —
    Phase 2 assigns personal-team scope at create)."""
    from app.services.library import chat_upload as m

    async def fake_fetch_one(sql: str, params=None):
        return {"team_id": 7001}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    scope = await m.resolve_chat_scope(session_id="888", user_id="u1")

    assert scope == ("team", "7001")
