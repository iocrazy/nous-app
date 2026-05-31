"""Verify mig 231: ai_sessions.id is BIGINT Snowflake, FK dependents rebuilt.

Tests that reference tables only present on prod (issues, ai_session_memory,
agent_runs) are skipped gracefully when those tables are absent from the local
dev DB.  On prod (INTEGRATION_DATABASE_URL or SUPAVISOR_DATABASE_URL) all
tables must exist, so the full assertion fires.
"""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

# Mark as integration; CI skips via env-var guard.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _table_exists(table: str) -> bool:
    rows = await db_engine.fetch_all(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t",
        {"t": table},
    )
    return len(rows) > 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_sessions_id_is_bigint():
    rows = await db_engine.fetch_all(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='ai_sessions' AND column_name='id'",
        {},
    )
    assert len(rows) == 1, "ai_sessions.id column not found"
    assert rows[0]["data_type"] == "bigint"


@pytest.mark.asyncio
async def test_ai_messages_session_id_is_bigint():
    """ai_messages is always present (mig 121); no skip guard needed."""
    rows = await db_engine.fetch_all(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='ai_messages' AND column_name='session_id'",
        {},
    )
    assert len(rows) == 1, "ai_messages.session_id column not found"
    assert rows[0]["data_type"] == "bigint"


@pytest.mark.asyncio
async def test_fk_dependents_are_bigint():
    """Check all 4 FK columns. Skip tables absent from the local dev DB."""
    targets = [
        ("issues", "ai_session_id"),
        ("ai_session_memory", "session_id"),
        ("agent_runs", "session_id"),
        ("ai_messages", "session_id"),
    ]
    for table, col in targets:
        if not await _table_exists(table):
            pytest.skip(f"{table} not present in this DB — skipping column check")
            continue
        rows = await db_engine.fetch_all(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t AND column_name=:c",
            {"t": table, "c": col},
        )
        assert len(rows) == 1, f"{table}.{col} column not found"
        assert rows[0]["data_type"] == "bigint", f"{table}.{col} should be bigint"


@pytest.mark.asyncio
async def test_fk_constraints_rebuilt():
    """Verify FK constraints exist for all present dependent tables."""
    # Always-present constraint
    always_present = {"ai_messages_session_id_fkey"}

    # Conditionally present constraints
    conditional = {
        "issues": "issues_ai_session_id_fkey",
        "ai_session_memory": "ai_session_memory_session_id_fkey",
        "agent_runs": "agent_runs_session_id_fkey",
    }

    expected = set(always_present)
    for table, fkey in conditional.items():
        if await _table_exists(table):
            expected.add(fkey)

    rows = await db_engine.fetch_all(
        "SELECT conname FROM pg_constraint "
        "WHERE contype = 'f' "
        "  AND conname IN ("
        "      'issues_ai_session_id_fkey',"
        "      'ai_session_memory_session_id_fkey',"
        "      'agent_runs_session_id_fkey',"
        "      'ai_messages_session_id_fkey'"
        "  )",
        {},
    )
    found = {r["conname"] for r in rows}
    assert found == expected, f"missing FK constraints: {expected - found}"


@pytest.mark.asyncio
async def test_ai_session_memory_primary_key_rebuilt():
    if not await _table_exists("ai_session_memory"):
        pytest.skip("ai_session_memory not present in this DB")
    rows = await db_engine.fetch_all(
        """
        SELECT kcu.column_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema = kcu.table_schema
         WHERE tc.constraint_type = 'PRIMARY KEY'
           AND tc.table_schema = 'public'
           AND tc.table_name = 'ai_session_memory'
        """,
        {},
    )
    assert len(rows) == 1, "ai_session_memory primary key not found"
    assert rows[0]["column_name"] == "session_id"


@pytest.mark.asyncio
async def test_ai_sessions_default_is_snowflake():
    """New rows must get a non-null BIGINT id without specifying it explicitly."""
    row_id = await db_engine.fetch_val(
        """
        INSERT INTO public.ai_sessions (user_id, title)
        VALUES (gen_random_uuid(), 'mig231-test-session')
        RETURNING id
        """,
        {},
    )
    assert row_id is not None, "INSERT did not return a BIGINT id"
    assert isinstance(row_id, int), f"expected int, got {type(row_id)}"
    assert row_id > 0

    # Cleanup
    await db_engine.execute(
        "DELETE FROM public.ai_sessions WHERE id = :id",
        {"id": row_id},
    )
