"""Task 1 (P3 W0): ``chat_upload._get_session_team_id`` conversations-first,
ai_sessions-fallback lookup.

Mirrors PR #960's ``write_memory._resolve_team_workspace`` dual-lookup shape
(see ``tests/test_write_memory_resolve_team_workspace.py``): snowflake ids
are minted exactly once by ``generate_snowflake_id()``, so a given
``session_id`` can never exist as a row in BOTH ``conversations`` and
``ai_sessions`` — the conversations query is tried first and short-circuits
on a hit; a miss falls back to the legacy ai_sessions lookup (byte-unchanged
SQL/params).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_session_team_id_tries_conversations_first(
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
async def test_get_session_team_id_falls_back_to_ai_sessions_when_no_conversations_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.library import chat_upload as m

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        if "public.conversations" in sql:
            return None
        return {"team_id": 42}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    team_id = await m._get_session_team_id("888")

    assert team_id == 42
    assert len(calls) == 2
    assert "public.conversations" in calls[0]["sql"]
    assert calls[1]["sql"] == "SELECT team_id FROM public.ai_sessions WHERE id = :id"
    assert calls[1]["params"] == {"id": 888}


@pytest.mark.asyncio
async def test_get_session_team_id_returns_none_when_neither_table_has_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.library import chat_upload as m

    async def fake_fetch_one(sql: str, params=None):
        return None

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    assert await m._get_session_team_id("888") is None


@pytest.mark.asyncio
async def test_get_session_team_id_returns_none_when_ai_sessions_row_has_null_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A found ai_sessions row with a NULL team_id (personal legacy session)
    stays authoritative — no further fallback, returns None."""
    from app.services.library import chat_upload as m

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql})
        if "public.conversations" in sql:
            return None
        return {"team_id": None}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    assert await m._get_session_team_id("888") is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_get_session_team_id_conversations_row_with_team_honored_by_resolve_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a conversations-store session_id resolves to
    ("team", str(team_id)) via resolve_chat_scope, proving the fix threads
    through to the public contract (a personal conversation's scope_id IS a
    team_id — Phase 2 assigns personal-team scope at create)."""
    from app.services.library import chat_upload as m

    async def fake_fetch_one(sql: str, params=None):
        if "public.conversations" in sql:
            return {"team_id": 7001}
        raise AssertionError("should not fall back to ai_sessions on a hit")

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)

    scope = await m.resolve_chat_scope(session_id="888", user_id="u1")

    assert scope == ("team", "7001")
