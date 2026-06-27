"""Live-DB integration test for agent_memory scoped recall (Phase A go-live gate).

The Phase A unit tests mock ``read_scope``, so the real asyncpg bind path was
never exercised against Postgres: the ``:user_id`` str→UUID coercion, the
``team_id = ANY(:team_ids)`` empty-array path, and the ``ts_rank`` ranking. This
test runs ``recall_rows`` against a real PG with the ``agent_memory`` table
(migration 323) to catch those edges before ``FEATURE_AGENT_MEMORY`` flips on.

Setup: requires INTEGRATION_DATABASE_URL set to a PG that has run migration 323.

    export INTEGRATION_DATABASE_URL="postgresql://postgres:...@host:port/postgres"
    uv run pytest tests/integration/test_agent_memory_recall_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset (CI has no DB → skipped).
All rows are owned by fixed test UUIDs and deleted after each test even on
failure.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Two distinct owners — the second proves a non-owner never sees private rows.
_OWNER_A = "11111111-1111-1111-1111-111111111111"
_OWNER_B = "22222222-2222-2222-2222-222222222222"
_AGENT = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine + sessionmaker at the test DSN so
    read_scope() (used by recall_rows) hits the test DB."""
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def seeded_rows(integration_db_url):
    """Insert a few PRIVATE agent_memory rows for OWNER_A, yield, then delete
    everything for both test owners (even on failure)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM public.agent_memory WHERE owner_user_id = ANY($1::uuid[])",
            [_OWNER_A, _OWNER_B],
        )
        for title, body, when in [
            ("deploy runbook", "run docker compose up -d backend", "when deploying"),
            (
                "db migration steps",
                "psql -U postgres -f migrations",
                "when migrating db",
            ),
            ("prefers terse replies", "keep answers short", "always"),
        ]:
            await conn.execute(
                """
                INSERT INTO public.agent_memory
                    (scope, owner_user_id, agent_id, visibility, kind, title,
                     body_md, when_to_use, fingerprint)
                VALUES ('agent_user', $1::uuid, $2::uuid, 'private', 'fact',
                        $3, $4, $5, '')
                """,
                _OWNER_A,
                _AGENT,
                title,
                body,
                when,
            )
        yield
    finally:
        await conn.execute(
            "DELETE FROM public.agent_memory WHERE owner_user_id = ANY($1::uuid[])",
            [_OWNER_A, _OWNER_B],
        )
        await conn.close()


async def test_recall_returns_ranked_owner_rows(patched_engine, seeded_rows):
    """Owner A recalls their own private rows, ranked by relevance — exercises
    the str→UUID bind, the empty team_ids array, and ts_rank on a real PG."""
    from app.services.ai.memory.agent_memory import MemoryContext, recall

    ctx = MemoryContext(user_id=_OWNER_A, team_ids=())
    hits = await recall(ctx, "deploy backend", limit=5)

    assert hits, "owner should recall their seeded rows"
    # The deploy runbook is the most relevant hit for 'deploy backend'.
    assert hits[0].title == "deploy runbook"
    assert hits[0].score > 0.0
    # Every hit belongs to the owner's private set (titles we seeded).
    seeded_titles = {"deploy runbook", "db migration steps", "prefers terse replies"}
    assert all(h.title in seeded_titles for h in hits)


async def test_recall_isolates_non_owner(patched_engine, seeded_rows):
    """A different user (owner B), with no teams, must NOT see owner A's private
    rows — the core isolation guarantee against a real DB."""
    from app.services.ai.memory.agent_memory import MemoryContext, recall

    ctx = MemoryContext(user_id=_OWNER_B, team_ids=())
    hits = await recall(ctx, "deploy backend", limit=5)

    assert hits == [], "a non-owner must not recall another user's private memory"
