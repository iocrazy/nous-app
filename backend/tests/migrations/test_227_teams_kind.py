"""Verify mig 227 adds teams.kind column + unique index."""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

# Mark these as integration tests — they need a live PG with migrations
# 227-230 applied. CI's pytest run skips them via the env-var guard below
# (CI doesn't set SUPAVISOR_DATABASE_URL).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]


@pytest.mark.asyncio
async def test_teams_kind_column_exists():
    rows = await db_engine.fetch_all(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='teams' AND column_name='kind'",
        {},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["data_type"] == "text"
    assert row["is_nullable"] == "NO"
    assert "'collaborative'" in (row["column_default"] or "")


@pytest.mark.asyncio
async def test_unique_personal_team_per_owner():
    rows = await db_engine.fetch_all(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename='teams' "
        "AND indexname='uq_teams_owner_personal'",
        {},
    )
    assert len(rows) == 1
    assert "kind = 'personal'" in rows[0]["indexdef"]
