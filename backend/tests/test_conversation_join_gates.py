"""Who may still join a conversation (OpenAPI P7).

- A dissolved (archived) group keeps its member rows, but takes no new user
  and no new agent: ``add_members`` / ``add_agent`` answer "not found", the
  same answer the management routes give for it.
- A 1:1 AI thread (``direct_agent``) and a user ``dm`` are fixed pairs.
  ``add_members`` already refused them; ``add_agent`` did not, so a second
  agent could be put into a 1:1 AI thread.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

from app.services.conversation_service import ConversationService

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"


def _repo(conv_type: str = "group", archived_at: object = None) -> AsyncMock:
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.is_team_member.return_value = True
    repo.conversation_scope_and_type.return_value = {
        "scope_id": 99,
        "type": conv_type,
        "archived_at": archived_at,
    }
    repo.add_members.return_value = 1
    repo.add_agent_member.return_value = None
    return repo


async def _add_agent(repo: AsyncMock) -> dict:
    agent = {
        "id": "00000000-0000-0000-0000-0000000000a1",
        "slug": "bot",
        "is_system_preset": True,
        "capability_profile": {"chat": {"enabled": True}},
    }
    agent_repo = AsyncMock()
    agent_repo.get_by_slug.return_value = agent
    with patch(
        "app.services.conversation_service.get_agent_repository",
        return_value=agent_repo,
    ):
        return await ConversationService(repo=repo).add_agent(
            conversation_id=1, user_id=USER, agent_slug="bot"
        )


ARCHIVED = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


@pytest.mark.asyncio
async def test_add_members_refuses_a_dissolved_group() -> None:
    repo = _repo(archived_at=ARCHIVED)
    with pytest.raises(ValueError, match="not found"):
        await ConversationService(repo=repo).add_members(
            conversation_id=1, user_id=USER, user_ids=["u2"]
        )
    repo.add_members.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_members_open_group_still_works() -> None:
    repo = _repo()
    added = await ConversationService(repo=repo).add_members(
        conversation_id=1, user_id=USER, user_ids=["u2"]
    )
    assert added == 1


@pytest.mark.asyncio
async def test_add_agent_refuses_a_dissolved_group() -> None:
    repo = _repo(archived_at=ARCHIVED)
    with pytest.raises(ValueError, match="not found"):
        await _add_agent(repo)
    repo.add_agent_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("conv_type", ["direct_agent", "dm"])
async def test_add_agent_refuses_fixed_pairs(conv_type: str) -> None:
    repo = _repo(conv_type=conv_type)
    with pytest.raises(PermissionError, match="conversation type"):
        await _add_agent(repo)
    repo.add_agent_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("conv_type", ["group", "public"])
async def test_add_agent_open_groups_still_work(conv_type: str) -> None:
    repo = _repo(conv_type=conv_type)
    out = await _add_agent(repo)
    assert out["added"] is True
