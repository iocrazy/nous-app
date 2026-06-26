"""Integration tests for ChatRepository agent-membership + turn context + mention fanout.

Proves:
  - add_agent_to_channel inserts into agent_channels (idempotent via ON CONFLICT).
  - is_agent_in_channel returns True after add, False before.
  - list_channel_agent_ids returns all agent IDs for the channel.
  - recent_messages returns the newest N messages in ASCENDING seq order.
  - increment_mentions bumps mention_count only for the named members.
  - get_channel returns id, team_id, type, history_mode, last_message_seq.

Requires INTEGRATION_DATABASE_URL. Skips cleanly if not set:

    export INTEGRATION_DATABASE_URL=postgresql://...
    uv run pytest tests/integration/test_chat_agent_channel.py -v -m integration
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
_TEAM_PREFIX = "__test_chat_agent_"


@pytest.fixture
async def chat_team_and_user():
    """Insert a throwaway team (owner auto-added to team_members by trigger).

    Patches the SQLAlchemy engine to point at INTEGRATION_DATABASE_URL so the
    repository works against the same DB.  Yields (team_id: int, user_id: str).
    Cleans up the team (CASCADE drops channels + members + agent_channels) on exit.
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


@pytest.fixture
async def test_agent_id(chat_team_and_user):
    """Insert a throwaway ai_agents row; yield its UUID string.

    Depends on chat_team_and_user so the engine patch is already live.
    Cleanup cascades agent_channels via agent_id FK.
    """
    conn = await asyncpg.connect(_TEST_DSN)
    agent_db_id = None
    try:
        agent_db_id = await conn.fetchval(
            "INSERT INTO ai_agents (name, persona) VALUES ($1, $2) RETURNING id",
            f"__test_agent_{uuid.uuid4().hex[:8]}",
            "Test agent for integration tests",
        )
        yield str(agent_db_id)
    finally:
        if agent_db_id is not None:
            await conn.execute("DELETE FROM ai_agents WHERE id = $1", agent_db_id)
        await conn.close()


# ─── add_agent_to_channel + is_agent_in_channel ─────────────────────────────


async def test_add_agent_is_in_channel(chat_team_and_user, test_agent_id):
    """add_agent_to_channel inserts a row; is_agent_in_channel returns True."""
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user
    agent_id = test_agent_id

    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="AgentTest",
        history_mode="shared",
        member_ids=[],
    )
    cid = ch["id"]

    assert not await repo.is_agent_in_channel(channel_id=cid, agent_id=agent_id)

    await repo.add_agent_to_channel(channel_id=cid, agent_id=agent_id, added_by=uid)
    assert await repo.is_agent_in_channel(channel_id=cid, agent_id=agent_id)

    # Idempotent second add must not raise
    await repo.add_agent_to_channel(channel_id=cid, agent_id=agent_id, added_by=uid)
    assert await repo.is_agent_in_channel(channel_id=cid, agent_id=agent_id)


# ─── list_channel_agent_ids ──────────────────────────────────────────────────


async def test_list_channel_agent_ids(chat_team_and_user):
    """list_channel_agent_ids returns all agent IDs added to the channel."""
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user

    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="AgentList",
        history_mode="shared",
        member_ids=[],
    )
    cid = ch["id"]

    # Start with no agents
    assert await repo.list_channel_agent_ids(channel_id=cid) == []

    # Insert two throwaway agents inline
    conn = await asyncpg.connect(_TEST_DSN)
    agent_ids: list[str] = []
    try:
        for i in range(2):
            aid = await conn.fetchval(
                "INSERT INTO ai_agents (name, persona) VALUES ($1, $2) RETURNING id",
                f"__test_list_agent_{i}_{uuid.uuid4().hex[:6]}",
                "Test",
            )
            agent_ids.append(str(aid))
            await repo.add_agent_to_channel(
                channel_id=cid, agent_id=str(aid), added_by=uid
            )
        result = await repo.list_channel_agent_ids(channel_id=cid)
        assert sorted(result) == sorted(agent_ids)
    finally:
        for aid in agent_ids:
            await conn.execute("DELETE FROM ai_agents WHERE id = $1::uuid", aid)
        await conn.close()


# ─── recent_messages returns ascending newest-N ───────────────────────────────


async def test_recent_messages_ascending(chat_team_and_user):
    """recent_messages returns the newest limit rows in ascending seq order."""
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user

    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="RecentMsgs",
        history_mode="shared",
        member_ids=[],
    )
    cid = ch["id"]

    # Send 5 messages
    for i in range(5):
        await repo.send_message(
            channel_id=cid,
            sender_id=uid,
            sender_type="user",
            content_type="text",
            body={"i": i},
            reply_to_id=None,
        )

    # Fetch 3 most recent — expect ascending order (oldest first among the 3)
    msgs = await repo.recent_messages(channel_id=cid, limit=3)
    assert len(msgs) == 3
    seqs = [m["seq"] for m in msgs]
    # newest 3 are seqs 3,4,5 — returned ascending
    assert seqs == sorted(
        seqs
    ), "recent_messages must return rows in ascending seq order"
    assert seqs[-1] == 5  # highest seq is last (most recent)
    assert seqs[0] == 3  # oldest of the 3


# ─── increment_mentions bumps only named members ────────────────────────────


async def test_increment_mentions_only_named_members(chat_team_and_user):
    """increment_mentions bumps mention_count only for the specified user_ids."""
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user

    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="MentionTest",
        history_mode="shared",
        member_ids=[],
    )
    cid = ch["id"]

    # Add a second member (no auth.users FK on channel_members.user_id)
    member2_id = str(uuid.uuid4())
    await repo.add_members(channel_id=cid, user_ids=[member2_id])

    # Mention only member2
    await repo.increment_mentions(channel_id=cid, user_ids=[member2_id])

    # Verify via direct asyncpg query
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        rows = await conn.fetch(
            "SELECT user_id::text, mention_count FROM channel_members "
            "WHERE channel_id = $1",
            cid,
        )
        counts = {r["user_id"]: r["mention_count"] for r in rows}
        assert counts[member2_id] == 1, "mentioned member should have mention_count=1"
        assert (
            counts.get(uid, 0) == 0
        ), "non-mentioned owner should have mention_count=0"
    finally:
        await conn.close()


async def test_increment_mentions_noop_on_empty(chat_team_and_user):
    """increment_mentions with an empty list is a no-op (no error, no update)."""
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user

    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="NoopMention",
        history_mode="shared",
        member_ids=[],
    )
    # Must not raise
    await repo.increment_mentions(channel_id=ch["id"], user_ids=[])


# ─── get_channel ─────────────────────────────────────────────────────────────


async def test_get_channel_returns_fields(chat_team_and_user):
    """get_channel returns the expected fields; returns None for unknown id."""
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user

    ch = await repo.create_channel(
        creator_id=uid,
        team_id=team_id,
        type="group",
        name="GetChanTest",
        history_mode="joined",
        member_ids=[],
    )
    cid = ch["id"]

    result = await repo.get_channel(channel_id=cid)
    assert result is not None
    assert result["id"] == cid
    assert result["team_id"] == team_id
    assert result["type"] == "group"
    assert result["history_mode"] == "joined"
    assert "last_message_seq" in result

    # Unknown channel → None
    assert await repo.get_channel(channel_id=999_999_999_999) is None
