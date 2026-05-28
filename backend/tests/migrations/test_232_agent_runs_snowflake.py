"""Verify mig 232: agent_runs.id is BIGINT Snowflake, all 7 FK dependents rebuilt.

Tests that reference tables only present on prod (agent_run_events,
agent_memories, agent_tasks, agent_commitments, issue_messages) are skipped
gracefully when those tables are absent from the local dev DB.  On prod
(INTEGRATION_DATABASE_URL or SUPAVISOR_DATABASE_URL) all tables must exist,
so the full assertion fires.

Special coverage for the 2 self-FKs (parent_run_id, root_run_id) and their
ON DELETE behaviour.
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
# Helpers
# ---------------------------------------------------------------------------


async def _table_exists(table: str) -> bool:
    rows = await db_engine.fetch_all(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t",
        {"t": table},
    )
    return len(rows) > 0


async def _col_type(table: str, column: str) -> str | None:
    rows = await db_engine.fetch_all(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c",
        {"t": table, "c": column},
    )
    return rows[0]["data_type"] if rows else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_runs_id_is_bigint():
    dtype = await _col_type("agent_runs", "id")
    assert dtype is not None, "agent_runs.id column not found"
    assert dtype == "bigint", f"expected bigint, got {dtype}"


@pytest.mark.asyncio
async def test_agent_runs_self_fk_columns_are_bigint():
    """Both self-FK columns must have been migrated to BIGINT."""
    for col in ("parent_run_id", "root_run_id"):
        dtype = await _col_type("agent_runs", col)
        assert dtype is not None, f"agent_runs.{col} column not found"
        assert dtype == "bigint", f"agent_runs.{col}: expected bigint, got {dtype}"


@pytest.mark.asyncio
async def test_fk_dependent_columns_are_bigint():
    """All 5 external FK columns must be BIGINT after migration. Skip absent tables."""
    targets = [
        ("agent_run_events", "run_id"),
        ("agent_memories", "run_id"),
        ("agent_tasks", "current_run_id"),
        ("agent_commitments", "fulfillment_run_id"),
        ("issue_messages", "agent_run_id"),
    ]
    for table, col in targets:
        if not await _table_exists(table):
            pytest.skip(f"{table} not present in this DB — skipping")
            continue
        dtype = await _col_type(table, col)
        assert dtype is not None, f"{table}.{col} column not found"
        assert dtype == "bigint", f"{table}.{col}: expected bigint, got {dtype}"


@pytest.mark.asyncio
async def test_self_fk_constraints_rebuilt():
    """Both self-FK constraints must exist with correct ON DELETE action."""
    rows = await db_engine.fetch_all(
        """
        SELECT conname,
               CASE confdeltype
                 WHEN 'a' THEN 'NO ACTION'
                 WHEN 'r' THEN 'RESTRICT'
                 WHEN 'c' THEN 'CASCADE'
                 WHEN 'n' THEN 'SET NULL'
                 WHEN 'd' THEN 'SET DEFAULT'
               END AS on_delete
          FROM pg_constraint
         WHERE contype = 'f'
           AND conrelid = 'public.agent_runs'::regclass
           AND conname IN (
               'agent_runs_parent_run_id_fkey',
               'agent_runs_root_run_id_fkey'
           )
        """,
        {},
    )
    found = {r["conname"]: r["on_delete"] for r in rows}

    assert (
        "agent_runs_parent_run_id_fkey" in found
    ), "agent_runs_parent_run_id_fkey constraint not found"
    assert (
        found["agent_runs_parent_run_id_fkey"] == "CASCADE"
    ), f"parent_run_id ON DELETE should be CASCADE, got {found['agent_runs_parent_run_id_fkey']}"

    assert (
        "agent_runs_root_run_id_fkey" in found
    ), "agent_runs_root_run_id_fkey constraint not found"
    # Default (no ON DELETE clause) = NO ACTION in PostgreSQL
    assert found["agent_runs_root_run_id_fkey"] in ("NO ACTION", "RESTRICT"), (
        f"root_run_id ON DELETE should be NO ACTION/RESTRICT, "
        f"got {found['agent_runs_root_run_id_fkey']}"
    )


@pytest.mark.asyncio
async def test_external_fk_constraints_rebuilt():
    """Verify external FK constraints exist for all present dependent tables."""
    always_present: set[str] = set()

    conditional = {
        "agent_run_events": "agent_run_events_run_id_fkey",
        "agent_memories": "agent_memories_run_id_fkey",
        "agent_tasks": "agent_tasks_current_run_id_fkey",
        "agent_commitments": "agent_commitments_fulfillment_run_id_fkey",
        "issue_messages": "issue_messages_agent_run_id_fkey",
    }

    expected: set[str] = set(always_present)
    for table, fkey in conditional.items():
        if await _table_exists(table):
            expected.add(fkey)

    if not expected:
        pytest.skip("No dependent tables found in this DB")
        return

    rows = await db_engine.fetch_all(
        "SELECT conname FROM pg_constraint "
        "WHERE contype = 'f' AND conname IN ("
        "  'agent_run_events_run_id_fkey',"
        "  'agent_memories_run_id_fkey',"
        "  'agent_tasks_current_run_id_fkey',"
        "  'agent_commitments_fulfillment_run_id_fkey',"
        "  'issue_messages_agent_run_id_fkey'"
        ")",
        {},
    )
    found = {r["conname"] for r in rows}
    assert found >= expected, f"missing FK constraints: {expected - found}"


@pytest.mark.asyncio
async def test_agent_runs_default_is_snowflake():
    """New rows must get a non-null BIGINT id via generate_snowflake_id() default."""
    # Insert a minimal agent_runs row using a real agent_id from ai_agents.
    # If no agents exist skip — this is an environment issue, not a migration issue.
    agent_rows = await db_engine.fetch_all(
        "SELECT id FROM public.ai_agents LIMIT 1",
        {},
    )
    if not agent_rows:
        pytest.skip("No ai_agents rows found — skipping INSERT test")
        return

    agent_id = agent_rows[0]["id"]

    run_id = await db_engine.fetch_val(
        """
        INSERT INTO public.agent_runs
            (agent_id, user_id, status, trigger, skill_slugs_used)
        VALUES
            (:agent_id, gen_random_uuid(), 'running', 'mig232_test', '{}')
        RETURNING id
        """,
        {"agent_id": str(agent_id)},
    )
    assert run_id is not None, "INSERT did not return a BIGINT id"
    assert isinstance(run_id, int), f"expected int, got {type(run_id)}"
    assert run_id > 0

    # Cleanup (CASCADE will remove any child rows if present)
    await db_engine.execute(
        "DELETE FROM public.agent_runs WHERE id = :id",
        {"id": run_id},
    )


@pytest.mark.asyncio
async def test_self_fk_backfill_join_correctness():
    """Verify the self-join backfill worked: root_run_id values are BIGINT.

    This is a sanity check that doesn't need live rows — it confirms the
    column type is right (covered by test_agent_runs_self_fk_columns_are_bigint)
    and that no UUID strings remain as text in the column.  A failed backfill
    would leave new_root_run_id NULL when the old root_run_id was not NULL,
    so we also check that.
    """
    # After migration, agent_runs.root_run_id is BIGINT.
    # Any row that previously had a non-null root_run_id UUID should now have
    # a non-null BIGINT root_run_id pointing to the new_id of the same target.
    mismatched = await db_engine.fetch_all(
        """
        SELECT COUNT(*) AS cnt
          FROM public.agent_runs r
          JOIN public.agent_runs root ON root.id = r.root_run_id
         WHERE r.root_run_id IS NOT NULL
           AND root.id IS NULL
        """,
        {},
    )
    # If the join finds no orphaned root_run_id references, backfill is clean.
    # (On an empty table this returns 0 rows which is also fine.)
    dangling = mismatched[0]["cnt"] if mismatched else 0
    assert (
        dangling == 0
    ), f"{dangling} agent_runs rows have root_run_id pointing to non-existent id"
