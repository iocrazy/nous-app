"""Tests for Team Chat router and schemas."""

from unittest.mock import AsyncMock
from unittest.mock import patch as _patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_chat_schemas_importable():
    from app.schemas.chat import (
        ChannelCreate,
        ChannelOut,
        MarkReadIn,
        MemberAdd,
        MessageCreate,
        MessageOut,
    )

    c = ChannelCreate(type="group", name="Editing Crew", team_id=1, member_ids=[])
    assert c.type == "group"


def _client(svc):
    from app.api.chat_router import router
    from app.core.deps import get_auth

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    class _Auth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant
    return app, svc


def test_post_message_403_when_not_member():
    svc = AsyncMock()
    svc.post_message.side_effect = PermissionError("nope")
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app)
        r = c.post(
            "/api/v1/chat/channels/5/messages",
            json={"content_type": "text", "body": {"text": "hi"}},
        )
    assert r.status_code == 403


def test_post_message_ok():
    svc = AsyncMock()
    svc.post_message.return_value = {
        "id": 9,
        "channel_id": 5,
        "seq": 1,
        "sender_id": "u",
        "sender_type": "user",
        "content_type": "text",
        "body": {"text": "hi"},
        "reply_to_id": None,
        "edited_at": None,
        "deleted_at": None,
        "created_at": "2026-06-25T00:00:00Z",
    }
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app)
        r = c.post(
            "/api/v1/chat/channels/5/messages",
            json={"content_type": "text", "body": {"text": "hi"}},
        )
    assert r.status_code == 200
    assert r.json()["seq"] == 1
