"""Tests for Unified Conversations router and schemas (Task 7).

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


def _make_client(svc: Any) -> TestClient:
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
        client = _make_client(svc)
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
        client = _make_client(svc)
        # TestClient runs background tasks synchronously before returning
        client2 = TestClient(
            client.app, raise_server_exceptions=True  # type: ignore[attr-defined]
        )
        r = client2.post(
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
        client = _make_client(svc)
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
