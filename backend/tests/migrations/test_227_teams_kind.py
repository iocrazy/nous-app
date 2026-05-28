"""Verify mig 227 adds teams.kind column + unique index."""

from __future__ import annotations

import pytest

from app.db import engine as db_engine


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
