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
