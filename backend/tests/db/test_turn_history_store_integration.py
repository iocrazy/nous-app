"""fh5 A2: the store / sidecar statements the turn-history loader relies on.

The loader's unit tests stub these; only Postgres can say that the
"rows after the watermark, newest window" select really returns the right
rows in ascending order, that soft-deleted rows stay out, and that the
sidecar row is gone after ``delete``. Skips cleanly without
``INTEGRATION_DATABASE_URL`` (the shape test at the top always runs).
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
pytest.importorskip("asyncpg")
_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — turn-history store checks need a DB.",
)


def test_store_legacy_shape_carries_seq():
    """Every downstream consumer used to lose ``seq`` here (recon-a §1.1);
    the watermark loader and fork need it."""
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    row = {
        "id": 353_000_000_000_777,
        "conversation_id": 353_000_000_000_001,
        "seq": 17,
        "sender_type": "user",
        "body": {"text": "hi"},
        "created_at": None,
    }
    assert ConversationsAiStore._to_legacy_message_shape(row)["seq"] == 17


@pytest.fixture
async def orm_dsn():
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    import asyncpg

    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def convs(pg):
    """A direct_agent conversation with ten rows (seq 6 soft-deleted) and an
    empty group conversation."""
    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) RETURNING id",
        "Turn History Test Team",
        user_id,
        uuid.uuid4().hex[:10],
    )
    ids = {}
    for ctype in ("direct_agent", "group"):
        ids[ctype] = await pg.fetchval(
            "INSERT INTO conversations (type, scope_id, created_by) VALUES ($1, $2, $3) "
            "RETURNING id",
            ctype,
            team_id,
            user_id,
        )
    direct = ids["direct_agent"]
    for seq in range(1, 11):
        await pg.execute(
            "INSERT INTO messages (conversation_id, seq, sender_type, type, body, deleted_at) "
            "VALUES ($1, $2, $3, 'text', $4::jsonb, CASE WHEN $5 THEN now() END)",
            direct,
            seq,
            "user" if seq % 2 else "agent",
            json.dumps({"text": f"row {seq}"}),
            seq == 6,
        )
    try:
        yield ids
    finally:
        for cid in ids.values():
            await pg.execute("DELETE FROM messages WHERE conversation_id = $1", cid)
            await pg.execute("DELETE FROM conversations WHERE id = $1", cid)
        await pg.execute("DELETE FROM teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


@pytest.mark.integration
@pytest.mark.asyncio
@_skip
async def test_get_messages_after_watermark_newest_window(orm_dsn, convs):
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    store = ConversationsAiStore()
    sid = convs["direct_agent"]

    everything = await store.get_messages_after(session_id=sid, after_seq=4)
    assert [m["seq"] for m in everything] == [5, 7, 8, 9, 10]  # 6 is deleted
    assert everything[0]["content"] == "row 5" and everything[0]["role"] == "user"

    newest = await store.get_messages_after(session_id=sid, after_seq=4, limit=3)
    assert [m["seq"] for m in newest] == [8, 9, 10]  # newest window, ascending

    assert await store.get_messages_after(session_id=sid, after_seq=10) == []


@pytest.mark.integration
@pytest.mark.asyncio
@_skip
async def test_get_conversation_type(orm_dsn, convs):
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    store = ConversationsAiStore()
    assert await store.get_conversation_type(session_id=convs["direct_agent"]) == (
        "direct_agent"
    )
    assert await store.get_conversation_type(session_id=convs["group"]) == "group"
    assert await store.get_conversation_type(session_id=1) is None


@pytest.mark.integration
@pytest.mark.asyncio
@_skip
async def test_memory_repo_delete(orm_dsn, convs):
    from app.repositories.conversation_memory_repository import (
        ConversationMemoryRepository,
    )

    repo = ConversationMemoryRepository()
    sid = convs["direct_agent"]
    await repo.upsert(
        conversation_id=sid, summary_md="s", last_seq_summarized=4, model=None
    )
    assert (await repo.load(sid))["last_seq_summarized"] == 4

    assert await repo.delete(sid) is True
    assert await repo.load(sid) is None
    assert await repo.delete(sid) is False  # nothing left to delete

    # a fresh row can start from a LOWER watermark after invalidation
    await repo.upsert(
        conversation_id=sid, summary_md="t", last_seq_summarized=2, model=None
    )
    assert (await repo.load(sid))["last_seq_summarized"] == 2
