"""Task 6 / SM1-3: real-DB smoke proving ``SessionMemoryRepository`` (the
BIGINT-keyed ``ai_session_memory`` table) accepts a real ``conversations.id``
as its key with ZERO code change, now that migration 332 dropped
``ai_session_memory_session_id_fkey`` (the FK that pinned the column to
``ai_sessions.id``).

Before mig 332 this insert would 23503 (foreign key violation) — there was
no ``ai_sessions`` row for a ``conversations.id`` to satisfy the FK. This
test is the unlock proof: create a conversation via ``ConversationsAiStore``
(the real Task 3/4 store), then upsert/load/delete a session-memory row
keyed on that conversation's id.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    export INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
    uv run pytest tests/integration/test_session_memory_conversations_id_smoke.py -v
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_INTEGRATION_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_SMOKE_CREATOR_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"


@pytest.fixture
async def conversation_smoke_ctx():
    """Real-DB fixture: resolve/create a personal team + a real ai_agents
    row for _SMOKE_CREATOR_ID, create ONE conversation via
    ConversationsAiStore, and clean everything up afterwards (mirrors
    ``smoke_ctx`` in test_conversations_ai_store.py)."""
    if not _INTEGRATION_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", _INTEGRATION_DSN):
        conn = await asyncpg.connect(_INTEGRATION_DSN)
        created_team_id = None
        conv_id: int | None = None
        try:
            user_row = await conn.fetchrow(
                "SELECT id FROM auth.users WHERE id = $1", _SMOKE_CREATOR_ID
            )
            if not user_row:
                pytest.skip(
                    f"creator {_SMOKE_CREATOR_ID!r} not found in auth.users — "
                    "cannot run integration smoke"
                )

            agent_row = await conn.fetchrow(
                "SELECT id, slug FROM public.ai_agents WHERE slug IS NOT NULL LIMIT 1"
            )
            if not agent_row:
                pytest.skip("no ai_agents rows found — cannot run integration smoke")

            team_row = await conn.fetchrow(
                "SELECT id FROM public.teams WHERE owner_id = $1 AND kind = 'personal'",
                _SMOKE_CREATOR_ID,
            )
            if team_row:
                team_id = team_row["id"]
            else:
                team_id = await conn.fetchval(
                    """
                    INSERT INTO public.teams (name, owner_id, invite_code, kind)
                    VALUES ($1, $2, $3, 'personal')
                    RETURNING id
                    """,
                    "__smoke_personal_team_task6__",
                    _SMOKE_CREATOR_ID,
                    f"SMOKE6{int(time.time())}",
                )
                created_team_id = team_id

            from app.services.ai.chat.conversations_ai_store import (
                ConversationsAiStore,
            )

            store = ConversationsAiStore()
            created = await store.create_session(
                user_id=_SMOKE_CREATOR_ID,
                agent_slug=agent_row["slug"],
                agent_id=str(agent_row["id"]),
                title="__smoke_task6_session_memory__",
                project_id=None,
                team_id=team_id,
                context_type="script",
                context_id="smoke-task6",
            )
            conv_id = created["id"]

            yield {"conversation_id": conv_id}
        finally:
            if conv_id is not None:
                await conn.execute(
                    "DELETE FROM public.ai_session_memory WHERE session_id = $1",
                    conv_id,
                )
                await conn.execute(
                    "DELETE FROM public.conversations WHERE id = $1", conv_id
                )
            if created_team_id is not None:
                await conn.execute(
                    "DELETE FROM public.teams WHERE id = $1", created_team_id
                )
            await conn.close()

    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


async def test_session_memory_round_trips_on_a_real_conversations_id(
    conversation_smoke_ctx: dict,
) -> None:
    """Upsert → load → delete a session_memory row keyed by a REAL
    conversations.id. Pre-mig-332 this insert would violate
    ai_session_memory_session_id_fkey (no ai_sessions row exists for this
    id) — passing here proves the FK drop unlocked the new-store key
    space with zero SessionMemoryRepository code changes."""
    from app.repositories.session_memory_repository import (
        SessionMemoryRepository,
    )

    conv_id = conversation_smoke_ctx["conversation_id"]
    repo = SessionMemoryRepository()

    # Nothing yet.
    assert await repo.load(str(conv_id)) is None

    # Upsert — this is the write that would have 23503'd pre-mig-332.
    upserted = await repo.upsert(
        str(conv_id),
        body_md="# Task 6 smoke\nconversation-keyed session memory",
        sections_json={"title": "Task 6 smoke"},
        tokens_at_update=321,
        tool_calls_at_update=2,
        turns_at_update=1,
    )
    assert upserted is not None
    assert upserted.session_id == str(conv_id)
    assert upserted.version == 1
    assert upserted.body_md == "# Task 6 smoke\nconversation-keyed session memory"

    # Load reads it back.
    loaded = await repo.load(str(conv_id))
    assert loaded is not None
    assert loaded.session_id == str(conv_id)
    assert loaded.body_md == upserted.body_md
    assert loaded.tokens_at_last_update == 321

    # Delete removes it.
    assert await repo.delete(str(conv_id)) is True
    assert await repo.load(str(conv_id)) is None
