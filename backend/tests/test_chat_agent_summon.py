"""Unit tests for ChatService.add_agent + ChatService.dispatch_summons (Task 5).

All heavy runtime deps (DB, LLM, repo) are mocked so the suite runs offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.chat_service import ChatService

# ─── Constants ────────────────────────────────────────────────────────────────

_CHANNEL_ID = 77
_TEAM_ID = 7
_USER_ID = "user-abc"
_AGENT_ID = "agent-uuid-001"
_AGENT_SLUG = "script_ai"

_CHANNEL = {"id": _CHANNEL_ID, "team_id": _TEAM_ID}
_AGENT = {
    "id": _AGENT_ID,
    "slug": _AGENT_SLUG,
    "capability_profile": {
        "chat": {
            "enabled": True,
            "read_team_resources": False,
            "auto_broadcast": False,
            "allowed_team_ids": [],
        }
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_repo(**overrides) -> AsyncMock:
    """Build a ChatRepository mock with safe defaults."""
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.get_channel.return_value = _CHANNEL
    repo.list_channel_agent_ids.return_value = [_AGENT_ID]
    repo.send_message.return_value = {"id": 1, "seq": 2, "from_bot_agent_id": None}
    repo.add_agent_to_channel.return_value = None
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def _make_agent_repo(agent: dict | None = _AGENT) -> AsyncMock:
    """Build an AgentRepository mock that returns *agent* for any slug/id."""
    ar = AsyncMock()
    ar.get_by_slug.return_value = agent
    ar.get_by_id.return_value = agent
    return ar


def _enabled_caps(enabled: bool = True) -> MagicMock:
    from app.services.ai.permissions.agent_chat_caps import ChatCaps

    return ChatCaps(
        enabled=enabled,
        read_team_resources=False,
        auto_broadcast=False,
        allowed_team_ids=(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# add_agent tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_agent_non_member_raises():
    """Non-member caller → PermissionError before any agent look-up."""
    repo = _make_repo()
    repo.is_member.return_value = False
    agent_repo = _make_agent_repo()

    svc = ChatService(repo)
    with patch(
        "app.services.chat_service.get_agent_repository", return_value=agent_repo
    ):
        with pytest.raises(PermissionError, match="not a member"):
            await svc.add_agent(
                channel_id=_CHANNEL_ID,
                user_id=_USER_ID,
                agent_slug=_AGENT_SLUG,
            )

    agent_repo.get_by_slug.assert_not_awaited()
    repo.add_agent_to_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_agent_not_enabled_raises():
    """Member caller, agent exists but caps.enabled=False → PermissionError."""
    repo = _make_repo()
    agent_repo = _make_agent_repo()
    disabled_caps = _enabled_caps(enabled=False)

    svc = ChatService(repo)
    with (
        patch(
            "app.services.chat_service.get_agent_repository", return_value=agent_repo
        ),
        patch("app.services.chat_service.agent_chat_caps", return_value=disabled_caps),
    ):
        with pytest.raises(PermissionError, match="not enabled"):
            await svc.add_agent(
                channel_id=_CHANNEL_ID,
                user_id=_USER_ID,
                agent_slug=_AGENT_SLUG,
            )

    repo.add_agent_to_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_agent_not_allows_team_raises():
    """Member caller, agent enabled but restricted to a different team → PermissionError."""
    from app.services.ai.permissions.agent_chat_caps import ChatCaps

    repo = _make_repo()
    agent_repo = _make_agent_repo()
    restricted_caps = ChatCaps(
        enabled=True,
        read_team_resources=False,
        auto_broadcast=False,
        allowed_team_ids=(999,),  # different team
    )

    svc = ChatService(repo)
    with (
        patch(
            "app.services.chat_service.get_agent_repository", return_value=agent_repo
        ),
        patch(
            "app.services.chat_service.agent_chat_caps", return_value=restricted_caps
        ),
    ):
        with pytest.raises(PermissionError, match="not enabled"):
            await svc.add_agent(
                channel_id=_CHANNEL_ID,
                user_id=_USER_ID,
                agent_slug=_AGENT_SLUG,
            )

    repo.add_agent_to_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_agent_happy_path():
    """Member + enabled agent → repo.add_agent_to_channel called, returns dict."""
    repo = _make_repo()
    agent_repo = _make_agent_repo()
    caps = _enabled_caps(enabled=True)

    svc = ChatService(repo)
    with (
        patch(
            "app.services.chat_service.get_agent_repository", return_value=agent_repo
        ),
        patch("app.services.chat_service.agent_chat_caps", return_value=caps),
    ):
        result = await svc.add_agent(
            channel_id=_CHANNEL_ID,
            user_id=_USER_ID,
            agent_slug=_AGENT_SLUG,
        )

    assert result["added"] is True
    assert result["agent_id"] == str(_AGENT_ID)
    repo.add_agent_to_channel.assert_awaited_once_with(
        channel_id=_CHANNEL_ID,
        agent_id=_AGENT_ID,
        added_by=_USER_ID,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# dispatch_summons tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dispatch_summons_anti_loop():
    """MOST IMPORTANT: agent-authored message (from_bot_agent_id set) → [] immediately.

    run_channel_agent_turn must NOT be called.
    """
    repo = _make_repo()
    agent_repo = _make_agent_repo()

    bot_message = {
        "body": {"text": f"@{_AGENT_SLUG} what do you think?"},
        "from_bot_agent_id": _AGENT_ID,  # ← bot authored
    }

    svc = ChatService(repo)
    with (
        patch(
            "app.services.chat_service.get_agent_repository", return_value=agent_repo
        ),
        patch("app.services.chat_service.run_channel_agent_turn") as mock_turn,
    ):
        result = await svc.dispatch_summons(
            channel_id=_CHANNEL_ID,
            summoner_user_id=_USER_ID,
            message=bot_message,
        )

    assert result == []
    mock_turn.assert_not_called()
    # Repo should not have been queried at all
    repo.list_channel_agent_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_summons_human_mention_calls_turn():
    """Human message @-mentions an in-channel agent → turn called, reply posted."""
    repo = _make_repo()
    agent_repo = _make_agent_repo()

    human_message = {
        "body": {"text": f"@{_AGENT_SLUG} summarize this"},
        "from_bot_agent_id": None,
    }

    svc = ChatService(repo)
    with (
        patch(
            "app.services.chat_service.get_agent_repository", return_value=agent_repo
        ),
        patch(
            "app.services.chat_service.run_channel_agent_turn",
            new_callable=AsyncMock,
            return_value="Here is my summary.",
        ) as mock_turn,
    ):
        result = await svc.dispatch_summons(
            channel_id=_CHANNEL_ID,
            summoner_user_id=_USER_ID,
            message=human_message,
        )

    assert _AGENT_SLUG in result
    mock_turn.assert_awaited_once()
    repo.send_message.assert_awaited_once()
    call_kwargs = repo.send_message.call_args.kwargs
    assert call_kwargs["sender_type"] == "agent"
    assert call_kwargs["from_bot_agent_id"] == _AGENT_ID
    assert call_kwargs["body"] == {"text": "Here is my summary."}


@pytest.mark.asyncio
async def test_dispatch_summons_agent_not_in_channel_skipped():
    """Mention of a slug not in this channel → turn NOT called."""
    repo = _make_repo()
    repo.list_channel_agent_ids.return_value = []  # no agents in channel

    agent_repo = _make_agent_repo()

    human_message = {
        "body": {"text": f"@{_AGENT_SLUG} help"},
        "from_bot_agent_id": None,
    }

    svc = ChatService(repo)
    with (
        patch(
            "app.services.chat_service.get_agent_repository", return_value=agent_repo
        ),
        patch(
            "app.services.chat_service.run_channel_agent_turn",
            new_callable=AsyncMock,
        ) as mock_turn,
    ):
        result = await svc.dispatch_summons(
            channel_id=_CHANNEL_ID,
            summoner_user_id=_USER_ID,
            message=human_message,
        )

    assert result == []
    mock_turn.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_summons_one_failure_does_not_block_others():
    """Two agents mentioned; first raises, second still produces a reply."""
    from app.services.ai.permissions.agent_chat_caps import ChatCaps

    _AGENT_B_ID = "agent-uuid-002"
    _AGENT_B_SLUG = "summarizer"

    agent_a = dict(_AGENT, id=_AGENT_ID, slug=_AGENT_SLUG)
    agent_b = {
        "id": _AGENT_B_ID,
        "slug": _AGENT_B_SLUG,
        "capability_profile": {"chat": {"enabled": True, "allowed_team_ids": []}},
    }

    repo = _make_repo()
    repo.list_channel_agent_ids.return_value = [_AGENT_ID, _AGENT_B_ID]

    # agent_repo returns different agents based on ID
    agent_repo = AsyncMock()
    agent_repo.get_by_id.side_effect = lambda agent_id: {
        _AGENT_ID: agent_a,
        _AGENT_B_ID: agent_b,
    }.get(str(agent_id))

    human_message = {
        "body": {"text": f"@{_AGENT_SLUG} and @{_AGENT_B_SLUG} both answer"},
        "from_bot_agent_id": None,
    }

    caps = ChatCaps(enabled=True, read_team_resources=False, auto_broadcast=False)

    call_count = 0

    async def _turn_side_effect(*, agent_slug, summoner_user_id, channel):
        nonlocal call_count
        call_count += 1
        if agent_slug == _AGENT_SLUG:
            raise RuntimeError("LLM timeout")
        return "Agent B reply"

    svc = ChatService(repo)
    with (
        patch(
            "app.services.chat_service.get_agent_repository", return_value=agent_repo
        ),
        patch("app.services.chat_service.agent_chat_caps", return_value=caps),
        patch(
            "app.services.chat_service.run_channel_agent_turn",
            side_effect=_turn_side_effect,
        ),
    ):
        result = await svc.dispatch_summons(
            channel_id=_CHANNEL_ID,
            summoner_user_id=_USER_ID,
            message=human_message,
        )

    # Agent A failed, agent B replied
    assert _AGENT_B_SLUG in result
    assert _AGENT_SLUG not in result
    assert call_count == 2
