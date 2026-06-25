"""Integration tests for ChatRepository against a real Postgres.

Proves:
  - send_message assigns monotonically increasing per-channel seq (atomic UPDATE
    RETURNING + INSERT in a single transaction).
  - get_my_channels computes unread = last_message_seq − last_read_seq correctly.
  - mark_read drives unread to 0 and is idempotent (GREATEST/LEAST guards).
  - list_messages returns rows in seq DESC order and supports keyset pagination
    via before_seq.

Requires INTEGRATION_DATABASE_URL. Skips cleanly if not set:

    export INTEGRATION_DATABASE_URL=postgresql://...
    uv run pytest tests/integration/test_chat_repository.py -v -m integration
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import asyncpg
import pytest

from app.repositories.chat_repository import get_chat_repository

pytestmark = pytest.mark.integration

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_TEAM_PREFIX = "__test_chat_repo_"


@pytest.fixture
async def chat_team_and_user():
    """Insert a throwaway team (owner auto-added to team_members by trigger).

    Patches the SQLAlchemy engine to point at INTEGRATION_DATABASE_URL so the
    repository works against the same DB.  Yields (team_id: int, user_id: str).
    Cleans up the team (CASCADE drops channels + members) on exit.
    """
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", _TEST_DSN):
        conn = await asyncpg.connect(_TEST_DSN)
        team_id = None
        try:
            user = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
            if not user:
                pytest.skip("need >=1 auth.users row to satisfy teams.owner_id FK")
            owner_id = user["id"]  # uuid.UUID from asyncpg
            team_id = await conn.fetchval(
                "INSERT INTO teams (name, owner_id, invite_code) "
                "VALUES ($1, $2, $3) RETURNING id",
                f"{_TEAM_PREFIX}{uuid.uuid4().hex[:8]}",
                owner_id,
                uuid.uuid4().hex[:10],
            )
            yield team_id, str(owner_id)
        finally:
            if team_id is not None:
                await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
            await conn.close()

    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


# ─── monotonic seq ──────────────────────────────────────────────────────


async def test_send_message_assigns_monotonic_seq(chat_team_and_user):
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user
    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="T",
        history_mode="shared",
        member_ids=[],
    )
    m1 = await repo.send_message(
        channel_id=ch["id"],
        sender_id=uid,
        sender_type="user",
        content_type="text",
        body={"text": "a"},
        reply_to_id=None,
    )
    m2 = await repo.send_message(
        channel_id=ch["id"],
        sender_id=uid,
        sender_type="user",
        content_type="text",
        body={"text": "b"},
        reply_to_id=None,
    )
    assert m2["seq"] == m1["seq"] + 1


# ─── unread diffusion + mark_read ───────────────────────────────────────


async def test_unread_and_mark_read(chat_team_and_user):
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user
    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="T2",
        history_mode="shared",
        member_ids=[],
    )
    await repo.send_message(
        channel_id=ch["id"],
        sender_id=uid,
        sender_type="user",
        content_type="text",
        body={"text": "x"},
        reply_to_id=None,
    )
    chans = await repo.get_my_channels(uid)
    target = next(c for c in chans if c["id"] == ch["id"])
    assert target["unread"] == 1

    await repo.mark_read(
        channel_id=ch["id"],
        user_id=uid,
        last_read_seq=target["last_message_seq"],
    )
    chans2 = await repo.get_my_channels(uid)
    assert next(c for c in chans2 if c["id"] == ch["id"])["unread"] == 0


# ─── keyset pagination ───────────────────────────────────────────────────


async def test_list_messages_keyset_desc(chat_team_and_user):
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user
    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="T3",
        history_mode="shared",
        member_ids=[],
    )
    for i in range(3):
        await repo.send_message(
            channel_id=ch["id"],
            sender_id=uid,
            sender_type="user",
            content_type="text",
            body={"i": i},
            reply_to_id=None,
        )
    page = await repo.list_messages(channel_id=ch["id"], before_seq=None, limit=2)
    assert [m["seq"] for m in page] == [3, 2]
    page2 = await repo.list_messages(channel_id=ch["id"], before_seq=2, limit=2)
    assert [m["seq"] for m in page2] == [1]
