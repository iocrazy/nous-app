"""Tests for chat/issue upload scope resolution (Task 1).

Tests pin the public contract of resolve_chat_scope:
- Returns ("team", str(team_id)) when the session has a team.
- Returns ("personal", str(user_id)) when session has no team or session_id is None.
"""

from unittest.mock import AsyncMock

import pytest

from app.services.library import chat_upload as m


@pytest.mark.asyncio
async def test_resolve_scope_team_when_session_has_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=4242))
    assert await m.resolve_chat_scope(session_id="s1", user_id="u1") == ("team", "4242")


@pytest.mark.asyncio
async def test_resolve_scope_personal_when_no_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    assert await m.resolve_chat_scope(session_id=None, user_id="u1") == (
        "personal",
        "u1",
    )


@pytest.mark.asyncio
async def test_resolve_scope_personal_when_session_has_no_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    assert await m.resolve_chat_scope(session_id="s1", user_id="u1") == (
        "personal",
        "u1",
    )
