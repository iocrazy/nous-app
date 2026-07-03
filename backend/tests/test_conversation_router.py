"""Tests for Unified Conversations router and schemas.

Mocked tests use a FastAPI TestClient with an overridden service dependency
and a stubbed auth user — no real DB required.

The smoke test (marked 'smoke') requires a real local Supabase Postgres:
    SUPAVISOR_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
    FEATURE_CONVERSATIONS=true \
    uv run pytest tests/test_conversation_router.py -v -k smoke
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import patch as _patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = "11111111-1111-1111-1111-111111111111"
_CONV_ID = 285274231427073  # snowflake bigint (fits as int literal)
_MSG_ID = 9
_SEQ = 1

# A representative MessageOut-shaped dict with sender_id as a plain string
# (the smoke test will exercise the real UUID→str coercion path).
_MSG_DICT: dict[str, Any] = {
    "id": _MSG_ID,
    "conversation_id": _CONV_ID,
    "seq": _SEQ,
    "sender_id": _USER_ID,
    "sender_type": "user",
    "type": "text",
    "body": {"text": "hello"},
    "parent_id": None,
    "edited_at": None,
    "deleted_at": None,
    "created_at": "2026-06-30T00:00:00Z",
}


def _make_client() -> TestClient:
    """Build a test FastAPI app that includes conversation_router directly
    (bypassing the flag-gate in api/__init__.py) and overrides get_auth."""
    from app.api.conversation_router import router
    from app.core.deps import get_auth

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    class _Auth:
        user_id = _USER_ID
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant
    return TestClient(app)


# ---------------------------------------------------------------------------
# Schema import sanity
# ---------------------------------------------------------------------------


def test_conversation_schemas_importable():
    from app.schemas.conversation import (
        AgentAdd,
        ConversationCreate,
        ConversationOut,
        MarkReadIn,
        MemberAdd,
        MessageCreate,
        MessageOut,
    )

    cc = ConversationCreate(
        type="group",
        scope_id=1,
        name="Test Room",
        history_mode="shared",
        member_ids=[],
    )
    assert cc.type == "group"

    # sender_id UUID → str coercion
    mo = MessageOut(
        id=1,
        conversation_id=2,
        seq=3,
        sender_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        sender_type="user",
        type="text",
        body={"text": "hi"},
        parent_id=None,
        edited_at=None,
        deleted_at=None,
        created_at="2026-06-30T00:00:00Z",
    )
    assert isinstance(mo.sender_id, str)
    assert mo.sender_id == "11111111-1111-1111-1111-111111111111"

    # None sender_id stays None
    mo2 = MessageOut(
        id=1,
        conversation_id=2,
        seq=3,
        sender_id=None,
        sender_type="agent",
        type="text",
        body={"text": "hi"},
        parent_id=None,
        edited_at=None,
        deleted_at=None,
        created_at="2026-06-30T00:00:00Z",
    )
    assert mo2.sender_id is None

    # BIGINT int → str coercion on MessageOut
    assert mo.id == "1"
    assert mo.conversation_id == "2"
    assert mo.seq == "3"


# ---------------------------------------------------------------------------
# POST /conversations/{conversation_id}/messages — 403 when not a member
# ---------------------------------------------------------------------------


def test_post_message_403_when_not_member():
    svc = AsyncMock()
    svc.post_message.side_effect = PermissionError("not a member")
    with _patch(
        "app.api.conversation_router.get_conversation_service", return_value=svc
    ):
        client = _make_client()
        r = client.post(
            f"/api/v1/conversations/{_CONV_ID}/messages",
            json={"type": "text", "body": {"text": "hi"}},
        )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /conversations/{conversation_id}/messages — 200 + schedules summon
# ---------------------------------------------------------------------------


def test_post_message_ok_and_schedules_summon():
    """Returns 200 with a valid MessageOut JSON body; background task calls
    dispatch_summons exactly once with the returned message dict."""
    svc = AsyncMock()
    svc.post_message.return_value = _MSG_DICT
    svc.dispatch_summons.return_value = []

    with _patch(
        "app.api.conversation_router.get_conversation_service", return_value=svc
    ):
        # TestClient runs background tasks synchronously before returning
        client = _make_client()
        r = client.post(
            f"/api/v1/conversations/{_CONV_ID}/messages",
            json={"type": "text", "body": {"text": "hello"}},
        )

    assert r.status_code == 200
    data = r.json()
    # seq and id come back as strings (BIGINT→str coercion)
    assert data["seq"] == str(_SEQ)
    assert data["type"] == "text"

    # Background task fires synchronously in TestClient
    svc.dispatch_summons.assert_called_once_with(
        conversation_id=_CONV_ID,
        summoner_user_id=_USER_ID,
        message=_MSG_DICT,
    )


# ---------------------------------------------------------------------------
# POST /conversations — ValueError → 400
# ---------------------------------------------------------------------------


def test_create_conversation_value_error_400():
    svc = AsyncMock()
    svc.create_conversation.side_effect = ValueError("bad type")
    with _patch(
        "app.api.conversation_router.get_conversation_service", return_value=svc
    ):
        client = _make_client()
        r = client.post(
            "/api/v1/conversations",
            json={
                "type": "group",
                "scope_id": _CONV_ID,
                "name": "Test",
                "history_mode": "shared",
                "member_ids": [],
            },
        )
    assert r.status_code == 400


def test_upload_attachment_ok():
    save = AsyncMock(
        return_value={
            "id": 123,
            "mime": "image/png",
            "file_size_bytes": 3,
        }
    )
    with _patch("app.api.conversation_router.save_chat_image", new=save):
        client = _make_client()
        r = client.post(
            f"/api/v1/conversations/{_CONV_ID}/attachments",
            files={"file": ("shot.png", b"PNG", "image/png")},
        )

    assert r.status_code == 200
    assert r.json() == {
        "id": "123",
        "mime": "image/png",
        "file_size_bytes": 3,
        "url": "/api/v1/generated-media/123/cover",
    }
    save.assert_awaited_once_with(
        conversation_id=_CONV_ID,
        user_id=_USER_ID,
        file_bytes=b"PNG",
        filename="shot.png",
        mime="image/png",
    )


def test_upload_attachment_rejects_non_image():
    save = AsyncMock()
    with _patch("app.api.conversation_router.save_chat_image", new=save):
        client = _make_client()
        r = client.post(
            f"/api/v1/conversations/{_CONV_ID}/attachments",
            files={"file": ("doc.txt", b"text", "text/plain")},
        )

    assert r.status_code == 400
    save.assert_not_called()


def test_promote_attachment_ok():
    class _FakePromoter:
        async def promote(self, *, gen_id, user_id, target_scope_id):
            assert gen_id == 123
            assert user_id == _USER_ID
            assert target_scope_id == 456
            return {"id": 789}

    with _patch(
        "app.api.conversation_router.PromoteGeneratedMediaService",
        return_value=_FakePromoter(),
    ):
        client = _make_client()
        r = client.post(
            "/api/v1/conversations/attachments/123/promote",
            json={"scope_id": 456},
        )

    assert r.status_code == 200
    assert r.json() == {"promoted_resource_id": "789"}


def test_promote_attachment_permission_error_403():
    class _FakePromoter:
        async def promote(self, *, gen_id, user_id, target_scope_id):
            raise PermissionError("not authorised")

    with _patch(
        "app.api.conversation_router.PromoteGeneratedMediaService",
        return_value=_FakePromoter(),
    ):
        client = _make_client()
        r = client.post(
            "/api/v1/conversations/attachments/123/promote",
            json={"scope_id": 456},
        )

    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Real-DB smoke test — requires local Supabase Postgres
# ---------------------------------------------------------------------------

_SMOKE_REASON = (
    "smoke: requires SUPAVISOR_DATABASE_URL pointing at local Supabase Postgres"
)
_has_db = bool(os.getenv("SUPAVISOR_DATABASE_URL"))


@pytest.mark.skipif(not _has_db, reason=_SMOKE_REASON)
def test_smoke_real_db_sender_id_coercion_and_null_before_seq():
    """Drive the ConversationService + ConversationRepository against a real
    asyncpg connection.  Proves:
      1. sender_id returned as uuid.UUID by asyncpg is coerced to str by
         MessageOut._coerce_sender_id.
      2. GET messages with before_seq=None (NULL) does not raise.
    """
    import asyncio

    from app.schemas.conversation import MessageOut
    from app.services.conversation_service import ConversationService

    _EXISTING_USER = "ca5e636f-6e60-414c-be54-110acf8c45c7"
    _TEAM_SCOPE_ID = 307991314617965

    async def _run():
        svc = ConversationService()

        # 1. Create a conversation
        conv = await svc.create_conversation(
            user_id=_EXISTING_USER,
            scope_id=_TEAM_SCOPE_ID,
            type="group",
            name="Smoke Test Conv",
            history_mode="shared",
            member_ids=[],
        )
        conv_id = conv["id"]

        # 2. Post a message (sender_id will come back from DB as UUID object)
        msg_row = await svc.post_message(
            conversation_id=conv_id,
            user_id=_EXISTING_USER,
            type="text",
            body={"text": "smoke test"},
            parent_id=None,
        )

        # 3. Validate sender_id coercion through MessageOut
        msg_out = MessageOut(**msg_row)
        assert isinstance(
            msg_out.sender_id, str
        ), f"sender_id should be str, got {type(msg_out.sender_id)}"
        assert msg_out.sender_id == _EXISTING_USER

        # 4. GET messages with before_seq=None — must not raise
        messages = await svc.get_messages(
            conversation_id=conv_id,
            user_id=_EXISTING_USER,
            before_seq=None,
            limit=10,
        )
        assert len(messages) >= 1
        # Coerce all rows through MessageOut
        for row in messages:
            mo = MessageOut(**row)
            assert isinstance(mo.id, str)
            assert isinstance(mo.seq, str)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# HTTP-layer smoke — exercises the full HTTP→response_model serialization path
# (the #918-class gap: asyncpg int8/UUID types only surface as 500s via HTTP)
# ---------------------------------------------------------------------------

_EXISTING_USER_HTTP = "ca5e636f-6e60-414c-be54-110acf8c45c7"
_TEAM_SCOPE_ID_HTTP = 307991314617965


def _make_http_smoke_client() -> TestClient:
    """Build a TestClient that includes conversation_router with a real-user
    auth override.  raise_server_exceptions=False so a serialization 500
    surfaces as a response (inspectable) rather than a Python exception."""
    from app.api.conversation_router import router
    from app.core.deps import get_auth

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    class _RealAuth:
        user_id = _EXISTING_USER_HTTP
        email = "smoke@example.com"

    async def _grant():
        return _RealAuth()

    app.dependency_overrides[get_auth] = _grant
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.skipif(not _has_db, reason=_SMOKE_REASON)
def test_smoke_http_layer_response_model_serialization():
    """Close the #918-class gap: exercises the FULL HTTP → response_model path
    so asyncpg int8/UUID 500s are caught here rather than in production.

    Steps:
      1. POST /conversations      — create a conversation
      2. POST /conversations/{id}/messages — post a text message
         Assert: 200 (NOT 500), sender_id is str or None
      3. GET  /conversations/{id}/messages  (no before_seq) — fetch messages
         Assert: returns the posted message, proving the NULL→CAST path works
    """
    client = _make_http_smoke_client()

    # 1. Create conversation
    r_create = client.post(
        "/api/v1/conversations/",
        json={
            "type": "group",
            "scope_id": _TEAM_SCOPE_ID_HTTP,
            "name": "HTTP Smoke Conv",
            "history_mode": "shared",
            "member_ids": [],
        },
    )
    assert (
        r_create.status_code == 200
    ), f"create conversation failed: {r_create.status_code} {r_create.text}"
    conv_id = r_create.json()["id"]

    # 2. Post a message — this is where asyncpg UUID/int8 types would trigger 500
    r_post = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"type": "text", "body": {"text": "http smoke"}},
    )
    assert (
        r_post.status_code == 200
    ), f"post message failed: {r_post.status_code} {r_post.text}"
    msg_data = r_post.json()
    # sender_id must be a string (str) or null — never a raw UUID object (which
    # would have caused a Pydantic serialization 500 before the fix)
    assert msg_data.get("sender_id") is None or isinstance(
        msg_data["sender_id"], str
    ), f"sender_id should be str or None, got {type(msg_data.get('sender_id'))}: {msg_data.get('sender_id')}"

    # 3. GET messages with no before_seq — exercises the NULL→CAST path over HTTP
    r_get = client.get(f"/api/v1/conversations/{conv_id}/messages")
    assert (
        r_get.status_code == 200
    ), f"get messages failed: {r_get.status_code} {r_get.text}"
    msgs = r_get.json()
    assert (
        isinstance(msgs, list) and len(msgs) >= 1
    ), f"expected at least 1 message, got: {msgs}"


def test_root_routes_do_not_redirect():
    """GET/POST /api/v1/conversations (no trailing slash) must match directly.

    A "/" route under the prefix 307-redirects the bare path; browsers block
    CORS preflight on redirects (and the proxied Location downgrades to http),
    which broke the go-live browser pass. TestClient follows redirects by
    default, so assert with follow_redirects=False.
    """
    svc = AsyncMock()
    svc.list_my_conversations.return_value = []
    with _patch(
        "app.api.conversation_router.get_conversation_service", return_value=svc
    ):
        client = _make_client()
        r = client.get("/api/v1/conversations", follow_redirects=False)
        assert r.status_code != 307, "bare GET /conversations must not redirect"
        assert r.status_code == 200
        r = client.post("/api/v1/conversations", json={}, follow_redirects=False)
        assert r.status_code != 307, "bare POST /conversations must not redirect"
