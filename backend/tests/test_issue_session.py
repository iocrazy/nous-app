"""get_or_create_issue_session returns the issue's session, creating one on
first call and reusing it after."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4



async def test_returns_existing_when_issue_has_session(monkeypatch):
    from app.services.issues import issue_session as m

    sid = str(uuid4())

    async def fake_fetch_one(sql, params=None):
        return {
            "ai_session_id": sid,
            "title": "t",
            "assignee_agent_id": str(uuid4()),
            "created_by_user_id": str(uuid4()),
            "assignee_user_id": None,
        }

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        got = await m.get_or_create_issue_session(409)
    assert got == sid


async def test_creates_and_backfills_when_absent(monkeypatch):
    from app.services.issues import issue_session as m

    agent_uuid, user_uuid = str(uuid4()), str(uuid4())
    state = {"calls": 0}

    async def fake_fetch_one(sql, params=None):
        state["calls"] += 1
        return {
            "ai_session_id": None,
            "title": "Write essay",
            "assignee_agent_id": agent_uuid,
            "created_by_user_id": user_uuid,
            "assignee_user_id": None,
        }

    chat_svc = AsyncMock()
    # ai_sessions.id is a BIGINT snowflake since mig 232 (service returns it
    # as a str) — the backfill must coerce to int for asyncpg.
    chat_svc.create_session = AsyncMock(return_value={"id": "315917457926636"})
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    agent_repo = AsyncMock()
    agent_repo.get_by_id = AsyncMock(return_value={"slug": "writer"})
    monkeypatch.setattr(m, "get_agent_repository", lambda: agent_repo)

    created = {}

    async def fake_execute(sql, params=None):
        created["sql"] = sql
        created["params"] = params
        return 1

    with (
        patch("app.db.engine.fetch_one", fake_fetch_one),
        patch("app.db.engine.execute", fake_execute),
    ):
        got = await m.get_or_create_issue_session(409)

    assert got == "315917457926636"
    assert "UPDATE public.issues" in created["sql"]
    # sid must be an int (BIGINT column; asyncpg rejects str)
    assert created["params"]["sid"] == 315917457926636
    assert created["params"]["id"] == 409


async def test_returns_none_when_no_agent(monkeypatch):
    from app.services.issues import issue_session as m

    async def fake_fetch_one(sql, params=None):
        return {
            "ai_session_id": None,
            "title": "t",
            "assignee_agent_id": None,
            "created_by_user_id": str(uuid4()),
            "assignee_user_id": None,
        }

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        assert await m.get_or_create_issue_session(1) is None
