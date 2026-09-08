"""DB-backed integration test for ConversationsAiStore's phase-2a question
methods (``latest_assistant_open_question`` / ``question_answered_stmt``).

The unit tests stub the session, so until this file nothing proved that
Postgres accepts the level-by-level ``||`` merge or that the newest-agent-
message SELECT returns the row the store expects. Same harness as
``tests/db/test_assets_repository_integration.py``; skips cleanly without
``INTEGRATION_DATABASE_URL``.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
pytest.importorskip("asyncpg")
_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — store question integration needs a DB.",
)

QUESTION = {
    "question_id": "q:1:2",
    "kind": "user",
    "prompt": "Which?",
    "options": [{"label": "A", "description": None}],
    "allow_free_text": False,
    "run_id": "1",
}


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
async def convo(pg):
    """One conversation with a user message and two agent messages; the NEWER
    agent message carries the open question. Torn down in a finally."""
    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) RETURNING id",
        "Question Store Test Team",
        user_id,
        uuid.uuid4().hex[:10],
    )
    conv_id = await pg.fetchval(
        "INSERT INTO conversations (type, scope_id, created_by) VALUES ('direct_agent', $1, $2) "
        "RETURNING id",
        team_id,
        user_id,
    )

    async def _msg(seq, sender_type, body):
        return await pg.fetchval(
            "INSERT INTO messages (conversation_id, seq, sender_type, type, body) "
            "VALUES ($1, $2, $3, 'text', $4::jsonb) RETURNING id",
            conv_id,
            seq,
            sender_type,
            json.dumps(body),
        )

    await _msg(1, "user", {"text": "hi"})
    older = await _msg(
        2,
        "agent",
        {
            "text": "older",
            "meta": {"awaiting_input": {**QUESTION, "question_id": "q:1:0"}},
        },
    )
    newest = await _msg(3, "agent", {"text": "", "meta": {"awaiting_input": QUESTION}})
    try:
        yield {"conv_id": conv_id, "older": older, "newest": newest}
    finally:
        await pg.execute("DELETE FROM messages WHERE conversation_id = $1", conv_id)
        await pg.execute("DELETE FROM conversations WHERE id = $1", conv_id)
        await pg.execute("DELETE FROM teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


@_skip
async def test_newest_agent_message_wins_and_answered_closes_it(orm_dsn, pg, convo):
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    store = ConversationsAiStore()
    found = await store.latest_assistant_open_question(session_id=str(convo["conv_id"]))
    assert found == {"message_id": convo["newest"], "question": QUESTION}

    await store.mark_question_answered(message_id=convo["newest"], value="A")
    body = json.loads(
        await pg.fetchval(
            "SELECT body::text FROM messages WHERE id = $1", convo["newest"]
        )
    )
    answered = body["meta"]["awaiting_input"]["answered"]
    assert (
        answered["value"] == "A" and answered["superseded"] is False and answered["at"]
    )
    # other keys at every level survive the merge
    assert body["meta"]["awaiting_input"]["question_id"] == "q:1:2"
    assert body["text"] == ""
    # ...and the question is now closed
    assert (
        await store.latest_assistant_open_question(session_id=str(convo["conv_id"]))
        is None
    )


@_skip
async def test_stamp_on_a_message_without_the_path_still_lands(orm_dsn, pg, convo):
    """The whole point of the level-by-level merge: no silent no-op."""
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    plain = await pg.fetchval(
        "INSERT INTO messages (conversation_id, seq, sender_type, type, body) "
        "VALUES ($1, 9, 'agent', 'text', '{\"text\": \"plain\"}'::jsonb) RETURNING id",
        convo["conv_id"],
    )
    await ConversationsAiStore().mark_question_answered(
        message_id=plain, value=None, superseded=True
    )
    body = json.loads(
        await pg.fetchval("SELECT body::text FROM messages WHERE id = $1", plain)
    )
    assert body["meta"]["awaiting_input"]["answered"]["superseded"] is True
    assert body["text"] == "plain"
