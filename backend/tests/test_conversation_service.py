"""Unit tests for ConversationService."""

from __future__ import annotations

import asyncio
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(**overrides) -> AsyncMock:
    """Return an AsyncMock ConversationRepository with sensible defaults."""
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.is_team_member.return_value = True
    repo.conversation_scope_id.return_value = 99
    repo.get_conversation.return_value = {
        "id": 1,
        "scope_id": 99,
        "type": "group",
        "history_mode": "shared",
        "last_seq": 0,
    }
    repo.get_my_conversations.return_value = []
    repo.create_conversation.return_value = {"id": 1, "scope_id": 99}
    repo.add_members.return_value = 1
    repo.send_message.return_value = {"id": 10, "seq": 1, "from_agent_id": None}
    repo.list_messages.return_value = []
    repo.mark_read.return_value = None
    repo.add_agent_member.return_value = None
    repo.list_conversation_agent_ids.return_value = []
    repo.edit_message.return_value = {"id": 10, "seq": 1}
    repo.soft_delete_message.return_value = {"id": 10, "seq": 1}
    repo.add_attachments.return_value = None
    repo.increment_mentions.return_value = None
    repo.recent_messages.return_value = []
    repo.list_member_ids.return_value = ["u1", "u2"]
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def _make_disabled_caps() -> MagicMock:
    caps = MagicMock()
    caps.enabled = False
    caps.allows_team.return_value = False
    return caps


def _make_enabled_caps(scope_id: int = 99) -> MagicMock:
    caps = MagicMock()
    caps.enabled = True
    caps.allows_team.return_value = True
    return caps


# ---------------------------------------------------------------------------
# 1. post_message — membership guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_requires_membership():
    """post_message must raise PermissionError when the user is not a member."""
    repo = _make_repo()
    repo.is_member.return_value = False

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    with pytest.raises(PermissionError, match="not a member"):
        await svc.post_message(
            conversation_id=1,
            user_id="outsider",
            type="text",
            body={"text": "hello"},
            parent_id=None,
        )
    repo.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_post_message_member_ok():
    """post_message succeeds when user is a member."""
    repo = _make_repo()
    repo.send_message.return_value = {"id": 5, "seq": 3, "from_agent_id": None}

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    result = await svc.post_message(
        conversation_id=1,
        user_id="u1",
        type="text",
        body={"text": "hello"},
        parent_id=None,
    )
    assert result["seq"] == 3
    repo.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_image_message_links_generated_media():
    repo = _make_repo()
    repo.send_message.return_value = {"id": 5, "seq": 3, "from_agent_id": None}

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    result = await svc.post_message(
        conversation_id=1,
        user_id="u1",
        type="image",
        body={"generated_media_id": "777", "kind": "image"},
        parent_id=None,
    )

    assert result["seq"] == 3
    repo.add_attachments.assert_awaited_once_with(
        message_id=5,
        generated_media_ids=[777],
    )


@pytest.mark.asyncio
async def test_post_image_message_malformed_generated_media_id_raises():
    repo = _make_repo()

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    with pytest.raises(ValueError):
        await svc.post_message(
            conversation_id=1,
            user_id="u1",
            type="image",
            body={"generated_media_id": "not-a-number", "kind": "image"},
            parent_id=None,
        )

    repo.send_message.assert_not_awaited()
    repo.add_attachments.assert_not_awaited()


# ---------------------------------------------------------------------------
# 2. dispatch_summons — anti-loop guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_summons_anti_loop_returns_empty():
    """dispatch_summons returns [] immediately when from_agent_id is set (not None)."""
    repo = _make_repo()

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    result = await svc.dispatch_summons(
        conversation_id=1,
        summoner_user_id="u1",
        message={
            "body": {"text": "@agent-slug hello"},
            "from_agent_id": "some-agent-uuid",
        },
    )
    assert result == []
    # Must not do any I/O — no DB calls should be made
    repo.get_conversation.assert_not_called()
    repo.list_conversation_agent_ids.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_summons_anti_loop_none_allowed():
    """dispatch_summons proceeds when from_agent_id is None (user message)."""
    repo = _make_repo()
    repo.list_conversation_agent_ids.return_value = []  # no agents → short-circuit

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    # Should NOT return early due to anti-loop (from_agent_id is None)
    # Will return [] because no agents are in the conversation
    result = await svc.dispatch_summons(
        conversation_id=1,
        summoner_user_id="u1",
        message={
            "body": {"text": "hello"},
            "from_agent_id": None,
        },
    )
    assert result == []
    repo.get_conversation.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. dispatch_summons — agent reply written to repo with from_agent_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_summons_writes_agent_reply(monkeypatch):
    """dispatch_summons calls repo.send_message with from_agent_id=agent["id"]."""
    agent_id = "agent-uuid-111"
    agent_slug = "test-agent"

    repo = _make_repo()
    repo.list_conversation_agent_ids.return_value = [agent_id]
    repo.send_message.return_value = {
        "id": 20,
        "seq": 5,
        "from_agent_id": agent_id,
    }

    # Fake agent repository
    fake_agent = {
        "id": agent_id,
        "slug": agent_slug,
        "agent_md": "",
        "identity_md": "",
    }
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_id.return_value = fake_agent

    # Fake agent_chat_caps — enabled for any team
    fake_caps = _make_enabled_caps()

    # Patch the name as it is bound in conversation_service (top-level import binding).
    with (
        patch(
            "app.services.conversation_service.run_conversation_agent_turn",
            new=AsyncMock(return_value="Great reply!"),
        ),
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps", return_value=fake_caps
        ),
        patch(
            "app.services.conversation_service.extract_agent_mentions",
            return_value=[agent_slug],
        ),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)
        result = await svc.dispatch_summons(
            conversation_id=1,
            summoner_user_id="u1",
            message={
                "body": {"text": f"@{agent_slug} help me"},
                "from_agent_id": None,
            },
        )

    assert result == [agent_slug]
    # Verify the agent reply was written with from_agent_id set
    repo.send_message.assert_awaited_once()
    call_kwargs = repo.send_message.call_args.kwargs
    assert call_kwargs["from_agent_id"] == agent_id
    assert call_kwargs["sender_type"] == "agent"
    assert call_kwargs["sender_id"] is None
    assert call_kwargs["body"] == {"text": "Great reply!"}


# ---------------------------------------------------------------------------
# 3b. dispatch_summons — parallel execution + post-turn compaction (Task 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_two_agents_runs_concurrently():
    """Two mentioned agents run under asyncio.gather (bounded by the
    cap-2 semaphore) rather than serially: wall time stays close to a
    single turn's duration, not their sum."""
    agent_a_id, agent_b_id = "agent-uuid-aaa", "agent-uuid-bbb"
    slug_a, slug_b = "agent-a", "agent-b"

    repo = _make_repo()
    repo.list_conversation_agent_ids.return_value = [agent_a_id, agent_b_id]
    repo.send_message.return_value = {"id": 30, "seq": 6, "from_agent_id": None}

    fake_agents = {
        agent_a_id: {
            "id": agent_a_id,
            "slug": slug_a,
            "agent_md": "",
            "identity_md": "",
        },
        agent_b_id: {
            "id": agent_b_id,
            "slug": slug_b,
            "agent_md": "",
            "identity_md": "",
        },
    }
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_id.side_effect = lambda aid: fake_agents[aid]

    fake_caps = _make_enabled_caps()

    async def _slow_turn(**kwargs):
        await asyncio.sleep(0.05)
        return "r"

    with (
        patch(
            "app.services.conversation_service.run_conversation_agent_turn",
            new=AsyncMock(side_effect=_slow_turn),
        ),
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps", return_value=fake_caps
        ),
        patch(
            "app.services.conversation_service.extract_agent_mentions",
            return_value=[slug_a, slug_b],
        ),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)
        start = time.perf_counter()
        result = await svc.dispatch_summons(
            conversation_id=1001,
            summoner_user_id="u1",
            message={
                "body": {"text": f"@{slug_a} @{slug_b} hi"},
                "from_agent_id": None,
            },
        )
        elapsed = time.perf_counter() - start

    assert elapsed < 0.09, f"expected parallel execution, took {elapsed:.3f}s"
    assert sorted(result) == sorted([slug_a, slug_b])


@pytest.mark.asyncio
async def test_dispatch_calls_maybe_compact_once_after_replies():
    """maybe_compact is awaited exactly once when >=1 agent replies, and
    NOT awaited when the anti-loop guard short-circuits or no agent
    replies."""
    agent_id = "agent-uuid-333"
    agent_slug = "compact-agent"

    repo = _make_repo()
    repo.list_conversation_agent_ids.return_value = [agent_id]
    repo.send_message.return_value = {"id": 40, "seq": 7, "from_agent_id": agent_id}

    fake_agent = {
        "id": agent_id,
        "slug": agent_slug,
        "agent_md": "",
        "identity_md": "",
    }
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_id.return_value = fake_agent
    fake_caps = _make_enabled_caps()

    mock_compact = AsyncMock()

    with (
        patch(
            "app.services.conversation_service.run_conversation_agent_turn",
            new=AsyncMock(return_value="a reply"),
        ),
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps", return_value=fake_caps
        ),
        patch(
            "app.services.conversation_service.extract_agent_mentions",
            return_value=[agent_slug],
        ),
        patch("app.services.conversation_service.maybe_compact", new=mock_compact),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)

        # Case 1: at least one reply -> maybe_compact awaited once.
        result = await svc.dispatch_summons(
            conversation_id=1002,
            summoner_user_id="u1",
            message={"body": {"text": f"@{agent_slug} hi"}, "from_agent_id": None},
        )
        assert result == [agent_slug]
        mock_compact.assert_awaited_once()
        mock_compact.reset_mock()

        # Case 2: anti-loop guard short-circuits -> not awaited.
        result = await svc.dispatch_summons(
            conversation_id=1002,
            summoner_user_id="u1",
            message={"body": {"text": "hi"}, "from_agent_id": "some-agent"},
        )
        assert result == []
        mock_compact.assert_not_awaited()

        # Case 3: no agent replies (turn returns falsy) -> not awaited.
        with patch(
            "app.services.conversation_service.run_conversation_agent_turn",
            new=AsyncMock(return_value=""),
        ):
            result = await svc.dispatch_summons(
                conversation_id=1002,
                summoner_user_id="u1",
                message={
                    "body": {"text": f"@{agent_slug} hi"},
                    "from_agent_id": None,
                },
            )
        assert result == []
        mock_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_agent_failure_does_not_sink_the_other():
    """First agent's turn raises; second agent's turn succeeds. Returned
    list is exactly [second]; no exception escapes dispatch_summons."""
    agent_a_id, agent_b_id = "agent-uuid-fail", "agent-uuid-ok"
    slug_a, slug_b = "fail-agent", "ok-agent"

    repo = _make_repo()
    repo.list_conversation_agent_ids.return_value = [agent_a_id, agent_b_id]
    repo.send_message.return_value = {"id": 50, "seq": 8, "from_agent_id": None}

    fake_agents = {
        agent_a_id: {
            "id": agent_a_id,
            "slug": slug_a,
            "agent_md": "",
            "identity_md": "",
        },
        agent_b_id: {
            "id": agent_b_id,
            "slug": slug_b,
            "agent_md": "",
            "identity_md": "",
        },
    }
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_id.side_effect = lambda aid: fake_agents[aid]
    fake_caps = _make_enabled_caps()

    async def _turn(*, agent_slug, summoner_user_id, conversation):
        if agent_slug == slug_a:
            raise RuntimeError("boom")
        return "I'm fine"

    with (
        patch(
            "app.services.conversation_service.run_conversation_agent_turn",
            new=AsyncMock(side_effect=_turn),
        ),
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps", return_value=fake_caps
        ),
        patch(
            "app.services.conversation_service.extract_agent_mentions",
            return_value=[slug_a, slug_b],
        ),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)
        result = await svc.dispatch_summons(
            conversation_id=1003,
            summoner_user_id="u1",
            message={
                "body": {"text": f"@{slug_a} @{slug_b} hi"},
                "from_agent_id": None,
            },
        )

    assert result == [slug_b]


# ---------------------------------------------------------------------------
# 4. add_agent — PermissionError when caps disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_summons_reply_write_failure_does_not_raise(monkeypatch):
    """dispatch_summons must not raise when repo.send_message fails after a reply."""
    agent_id = "agent-uuid-222"
    agent_slug = "error-agent"

    repo = _make_repo()
    repo.list_conversation_agent_ids.return_value = [agent_id]
    # send_message raises — simulates a DB outage during reply write
    repo.send_message.side_effect = Exception("db down")

    fake_agent = {
        "id": agent_id,
        "slug": agent_slug,
        "agent_md": "",
        "identity_md": "",
    }
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_id.return_value = fake_agent

    fake_caps = _make_enabled_caps()

    with (
        patch(
            "app.services.conversation_service.run_conversation_agent_turn",
            new=AsyncMock(return_value="I have a reply!"),
        ),
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps", return_value=fake_caps
        ),
        patch(
            "app.services.conversation_service.extract_agent_mentions",
            return_value=[agent_slug],
        ),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)
        # Must NOT raise — failed reply write is log-and-continue
        result = await svc.dispatch_summons(
            conversation_id=1,
            summoner_user_id="u1",
            message={
                "body": {"text": f"@{agent_slug} hi"},
                "from_agent_id": None,
            },
        )

    # Agent was not added to replied because write failed
    assert agent_slug not in result
    # send_message was attempted (the guard did not short-circuit before the call)
    repo.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# 4. add_agent — PermissionError when caps disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_agent_raises_when_caps_disabled():
    """add_agent raises PermissionError when agent_chat_caps.enabled is False."""
    repo = _make_repo()
    fake_agent = {"id": "a1", "slug": "bot", "agent_md": ""}
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_slug.return_value = fake_agent

    disabled_caps = _make_disabled_caps()

    with (
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps",
            return_value=disabled_caps,
        ),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)
        with pytest.raises(PermissionError, match="agent not enabled"):
            await svc.add_agent(
                conversation_id=1,
                user_id="u1",
                agent_slug="bot",
            )

    repo.add_agent_member.assert_not_called()


@pytest.mark.asyncio
async def test_add_agent_raises_when_scope_restricted():
    """add_agent raises PermissionError when caps.enabled=True but allows_team=False."""
    repo = _make_repo()
    fake_agent = {"id": "a2", "slug": "scoped-bot", "agent_md": ""}
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_slug.return_value = fake_agent

    # caps enabled but NOT allowed for this team scope
    scope_restricted_caps = MagicMock()
    scope_restricted_caps.enabled = True
    scope_restricted_caps.allows_team.return_value = False

    with (
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps",
            return_value=scope_restricted_caps,
        ),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)
        with pytest.raises(PermissionError, match="agent not enabled"):
            await svc.add_agent(
                conversation_id=1,
                user_id="u1",
                agent_slug="scoped-bot",
            )

    repo.add_agent_member.assert_not_called()


@pytest.mark.asyncio
async def test_add_agent_succeeds_when_caps_enabled():
    """add_agent succeeds when agent_chat_caps is enabled and allows the scope."""
    repo = _make_repo()
    fake_agent = {"id": "a1", "slug": "bot", "agent_md": ""}
    fake_agent_repo = AsyncMock()
    fake_agent_repo.get_by_slug.return_value = fake_agent

    enabled_caps = _make_enabled_caps()

    with (
        patch(
            "app.services.conversation_service.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch(
            "app.services.conversation_service.agent_chat_caps",
            return_value=enabled_caps,
        ),
    ):
        from app.services.conversation_service import ConversationService

        svc = ConversationService(repo)
        result = await svc.add_agent(
            conversation_id=1,
            user_id="u1",
            agent_slug="bot",
        )

    assert result["added"] is True
    assert result["agent_id"] == "a1"
    repo.add_agent_member.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. Additional coverage: create_conversation, get_messages, mark_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation_non_team_member_raises():
    """create_conversation raises PermissionError when caller not in the team."""
    repo = _make_repo()
    repo.is_team_member.return_value = False

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    with pytest.raises(PermissionError):
        await svc.create_conversation(
            user_id="outsider",
            scope_id=99,
            type="group",
            name="Test",
            history_mode="shared",
            member_ids=[],
        )
    repo.create_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_create_conversation_succeeds():
    """create_conversation works when the caller is a team member."""
    repo = _make_repo()
    repo.is_team_member.return_value = True
    repo.create_conversation.return_value = {
        "id": 7,
        "scope_id": 99,
        "type": "group",
    }

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    result = await svc.create_conversation(
        user_id="u1",
        scope_id=99,
        type="group",
        name="My Group",
        history_mode="shared",
        member_ids=["u2"],
    )
    assert result["id"] == 7
    repo.create_conversation.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_messages_requires_membership():
    """get_messages raises PermissionError for non-members."""
    repo = _make_repo()
    repo.is_member.return_value = False

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    with pytest.raises(PermissionError):
        await svc.get_messages(
            conversation_id=1,
            user_id="outsider",
            before_seq=None,
            limit=50,
        )


@pytest.mark.asyncio
async def test_get_messages_passes_for_user_id_to_repo():
    """ConversationService.get_messages must forward the caller's user_id as
    for_user_id so the repository can apply the joined-mode history cutoff
    (Phase-1 final-review carryover, mig 328 messages_select RLS parity)."""
    repo = _make_repo()

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    await svc.get_messages(
        conversation_id=1,
        user_id="u1",
        before_seq=None,
        limit=50,
    )

    repo.list_messages.assert_awaited_once_with(
        conversation_id=1,
        before_seq=None,
        limit=50,
        for_user_id="u1",
    )


@pytest.mark.asyncio
async def test_edit_message_not_found_raises():
    """edit_message raises PermissionError when repo returns None."""
    repo = _make_repo()
    repo.edit_message.return_value = None

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    with pytest.raises(PermissionError, match="not found or not editable"):
        await svc.edit_message(
            conversation_id=1,
            user_id="u1",
            message_id=42,
            body={"text": "updated"},
        )


@pytest.mark.asyncio
async def test_delete_message_not_found_raises():
    """delete_message raises PermissionError when repo returns None."""
    repo = _make_repo()
    repo.soft_delete_message.return_value = None

    from app.services.conversation_service import ConversationService

    svc = ConversationService(repo)
    with pytest.raises(PermissionError, match="not found or not deletable"):
        await svc.delete_message(
            conversation_id=1,
            user_id="u1",
            message_id=42,
        )
