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


_MSG = {
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


def test_post_message_ok():
    svc = AsyncMock()
    svc.post_message.return_value = _MSG
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app)
        r = c.post(
            "/api/v1/chat/channels/5/messages",
            json={"content_type": "text", "body": {"text": "hi"}},
        )
    assert r.status_code == 200
    assert r.json()["seq"] == 1


# ---------------------------------------------------------------------------
# PHASE-2: add-agent endpoint tests
# ---------------------------------------------------------------------------


def test_add_agent_403_on_permission_error():
    svc = AsyncMock()
    svc.add_agent.side_effect = PermissionError(
        "agent not enabled for chat in this team"
    )
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app)
        r = c.post(
            "/api/v1/chat/channels/5/agents",
            json={"agent_slug": "my-bot"},
        )
    assert r.status_code == 403
    assert "agent not enabled" in r.json()["detail"]


def test_add_agent_ok():
    svc = AsyncMock()
    svc.add_agent.return_value = {"added": True, "agent_id": "42"}
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app)
        r = c.post(
            "/api/v1/chat/channels/5/agents",
            json={"agent_slug": "my-bot"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["added"] is True
    assert data["agent_id"] == "42"


# ---------------------------------------------------------------------------
# PHASE-2: post_message schedules background summon
# ---------------------------------------------------------------------------


def test_post_message_schedules_summon():
    """post_message returns the human message synchronously and schedules
    _summon_runner as a background task (TestClient runs bg tasks before
    returning, so we can assert dispatch_summons was called)."""
    svc = AsyncMock()
    svc.post_message.return_value = _MSG
    svc.dispatch_summons.return_value = []
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app, raise_server_exceptions=True)
        r = c.post(
            "/api/v1/chat/channels/5/messages",
            json={"content_type": "text", "body": {"text": "hello"}},
        )
    assert r.status_code == 200
    assert r.json()["seq"] == 1
    # Background task runs synchronously in TestClient — dispatch_summons must
    # have been called exactly once with the created message.
    svc.dispatch_summons.assert_called_once_with(
        channel_id=5,
        summoner_user_id="11111111-1111-1111-1111-111111111111",
        message=_MSG,
    )
