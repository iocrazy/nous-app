"""``POST /conversations/{id}/agents`` only admits agents the caller can see.

Before OpenAPI P7 any member could add any chat-enabled agent by slug —
including another user's private agent, whose prompts then answered in the
caller's group on every @-mention. The visible set is the agent picker's
(``list_accessible``) and the P5 by-slug rule: system presets, plus agents in
the caller's own / team / project scope. An agent outside it is answered like
a missing slug, before the chat-caps check (no existence oracle).
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import app.api.ai_library_router  # noqa: F401 — loads the module
from app.services.conversation_service import ConversationService

# ``app.api`` re-exports the router object under the module's name.
ai_lib = sys.modules["app.api.ai_library_router"]

pytestmark = pytest.mark.unit

CALLER = "00000000-0000-0000-0000-000000000042"
STRANGER = "00000000-0000-0000-0000-000000000099"
TEAM = 555


def _repo() -> AsyncMock:
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.get_conversation.return_value = {
        "id": 1,
        "scope_id": 99,
        "type": "group",
        "history_mode": "shared",
        "last_seq": 0,
    }
    repo.add_agent_member.return_value = None
    return repo


def _agent(**overrides: Any) -> dict[str, Any]:
    agent = {
        "id": "00000000-0000-0000-0000-0000000000a1",
        "slug": "private-bot",
        "user_id": STRANGER,
        "team_id": None,
        "project_id": None,
        "is_system_preset": False,
        "capability_profile": {"chat": {"enabled": True}},
    }
    agent.update(overrides)
    return agent


async def _add(agent: dict[str, Any], repo: AsyncMock, monkeypatch, *, teams=()):
    async def is_team_member(user_uuid, team_id: int) -> bool:
        return int(team_id) in teams

    monkeypatch.setattr(ai_lib, "_user_is_team_member", is_team_member)
    monkeypatch.setattr(
        ai_lib, "_user_can_write_project", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(ai_lib, "_user_is_admin", AsyncMock(return_value=False))
    agent_repo = AsyncMock()
    agent_repo.get_by_slug.return_value = agent
    with patch(
        "app.services.conversation_service.get_agent_repository",
        return_value=agent_repo,
    ):
        return await ConversationService(repo=repo).add_agent(
            conversation_id=1, user_id=CALLER, agent_slug=agent["slug"]
        )


@pytest.mark.asyncio
async def test_someone_elses_private_agent_is_refused_like_a_missing_slug(
    monkeypatch,
) -> None:
    repo = _repo()
    with pytest.raises(ValueError, match="not found"):
        await _add(_agent(), repo, monkeypatch)
    repo.add_agent_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_refusal_precedes_the_caps_check(monkeypatch) -> None:
    """Chat disabled + out of scope must not answer "not enabled" (403)."""
    repo = _repo()
    with pytest.raises(ValueError, match="not found"):
        await _add(_agent(capability_profile={}), repo, monkeypatch)


@pytest.mark.asyncio
async def test_other_teams_agent_is_refused(monkeypatch) -> None:
    repo = _repo()
    with pytest.raises(ValueError, match="not found"):
        await _add(_agent(user_id=None, team_id=TEAM), repo, monkeypatch, teams=())
    repo.add_agent_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_own_agent_is_admitted(monkeypatch) -> None:
    repo = _repo()
    out = await _add(_agent(user_id=CALLER), repo, monkeypatch)
    assert out["added"] is True
    repo.add_agent_member.assert_awaited_once()


@pytest.mark.asyncio
async def test_team_agent_is_admitted_for_a_team_member(monkeypatch) -> None:
    repo = _repo()
    out = await _add(
        _agent(user_id=None, team_id=TEAM), repo, monkeypatch, teams=(TEAM,)
    )
    assert out["added"] is True


@pytest.mark.asyncio
async def test_system_preset_is_admitted(monkeypatch) -> None:
    repo = _repo()
    out = await _add(_agent(user_id=None, is_system_preset=True), repo, monkeypatch)
    assert out["added"] is True
