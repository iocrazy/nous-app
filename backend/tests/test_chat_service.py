from unittest.mock import AsyncMock

import pytest

from app.services.chat_service import ChatService


@pytest.mark.asyncio
async def test_post_message_requires_membership():
    repo = AsyncMock()
    repo.is_member.return_value = False
    svc = ChatService(repo)
    with pytest.raises(PermissionError):
        await svc.post_message(
            channel_id=1,
            user_id="u",
            content_type="text",
            body={"text": "hi"},
            reply_to_id=None,
        )
    repo.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_post_message_member_ok():
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.send_message.return_value = {"id": 9, "seq": 1}
    svc = ChatService(repo)
    out = await svc.post_message(
        channel_id=1,
        user_id="u",
        content_type="text",
        body={"text": "hi"},
        reply_to_id=None,
    )
    assert out["seq"] == 1
    repo.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_channel_non_member_raises_permission_error():
    repo = AsyncMock()
    repo.is_team_member.return_value = False
    svc = ChatService(repo)
    with pytest.raises(PermissionError):
        await svc.create_channel(
            user_id="u",
            team_id=42,
            type="group",
            name="Test",
            history_mode="shared",
            member_ids=[],
        )
    repo.create_channel.assert_not_called()


@pytest.mark.asyncio
async def test_create_channel_team_member_succeeds():
    repo = AsyncMock()
    repo.is_team_member.return_value = True
    repo.create_channel.return_value = {
        "id": 1,
        "team_id": 42,
        "type": "group",
        "history_mode": "shared",
        "name": "Test",
        "topic": None,
        "last_message_seq": 0,
        "created_at": "2026-06-25T00:00:00Z",
    }
    svc = ChatService(repo)
    result = await svc.create_channel(
        user_id="u",
        team_id=42,
        type="group",
        name="Test",
        history_mode="shared",
        member_ids=[],
    )
    assert result["id"] == 1
    repo.create_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_members_rejects_non_team_member():
    repo = AsyncMock()
    repo.is_member.return_value = True  # caller is a channel member
    repo.channel_team_id.return_value = 42
    # caller passes team check, target fails
    repo.is_team_member.side_effect = lambda *, team_id, user_id: (user_id == "caller")
    svc = ChatService(repo)
    with pytest.raises(PermissionError):
        await svc.add_members(
            channel_id=1,
            user_id="caller",
            user_ids=["outsider"],
        )
    repo.add_members.assert_not_called()


@pytest.mark.asyncio
async def test_add_members_all_valid_targets_succeeds():
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.channel_team_id.return_value = 42
    repo.is_team_member.return_value = True
    repo.add_members.return_value = 2
    svc = ChatService(repo)
    added = await svc.add_members(
        channel_id=1,
        user_id="caller",
        user_ids=["member1", "member2"],
    )
    assert added == 2
    repo.add_members.assert_awaited_once()
