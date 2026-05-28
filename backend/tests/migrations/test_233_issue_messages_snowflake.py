"""Verify mig 233: issue_messages.id is BIGINT Snowflake.

Tests gracefully handle issue_messages being absent from the local dev DB
(it may not exist if the local snapshot is behind). On prod (INTEGRATION_DATABASE_URL
or SUPAVISOR_DATABASE_URL) the table must exist, so the full assertions fire.
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
async def test_issue_messages_id_is_bigint():
    if not await _table_exists("issue_messages"):
        pytest.skip("issue_messages table not present in this DB")

    rows = await db_engine.fetch_all(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='issue_messages' AND column_name='id'",
        {},
    )
    assert len(rows) == 1, "issue_messages.id column not found"
    assert rows[0]["data_type"] == "bigint"


@pytest.mark.asyncio
async def test_issue_messages_primary_key_is_id():
    if not await _table_exists("issue_messages"):
        pytest.skip("issue_messages table not present in this DB")

    rows = await db_engine.fetch_all(
        """
        SELECT kcu.column_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema = kcu.table_schema
         WHERE tc.constraint_type = 'PRIMARY KEY'
           AND tc.table_schema = 'public'
           AND tc.table_name = 'issue_messages'
        """,
        {},
    )
    assert len(rows) == 1, "issue_messages primary key not found"
    assert rows[0]["column_name"] == "id"


@pytest.mark.asyncio
async def test_issue_messages_default_is_snowflake():
    """New rows must get a non-null BIGINT id without specifying it explicitly."""
    if not await _table_exists("issue_messages"):
        pytest.skip("issue_messages table not present in this DB")

    # Check that the column default is set to generate_snowflake_id()
    rows = await db_engine.fetch_all(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='issue_messages' AND column_name='id'",
        {},
    )
    assert len(rows) == 1, "issue_messages.id column not found"
    assert "generate_snowflake_id" in (
        rows[0]["column_default"] or ""
    ), f"id column default should be generate_snowflake_id(), got {rows[0]['column_default']}"
