"""Real-PG round trip for the mirror bind shape (rolled back). Requires
INTEGRATION_DATABASE_URL; skipped otherwise. Verified by hand on the
production DB 2026-09-05: old pattern → jsonb 'string', fixed → 'object'."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration
DSN = os.environ.get("INTEGRATION_DATABASE_URL")


@pytest.mark.skipif(not DSN, reason="INTEGRATION_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_mirror_lands_as_jsonb_object():
    from sqlalchemy import literal_column, select
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models.agents import AgentRuns
    from app.services.ai.runner.run_recorder import RunEventWriter

    dsn = DSN.replace("postgresql://", "postgresql+asyncpg://", 1).split("?")[0]
    eng = create_async_engine(dsn)
    async with eng.connect() as conn:
        trans = await conn.begin()
        run_id = (
            await conn.execute(
                select(AgentRuns.id).order_by(AgentRuns.started_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if run_id is None:
            pytest.skip("no agent_runs rows to round-trip against")
        w = RunEventWriter(int(run_id))
        await conn.execute(w.mirror_stmt())
        t = (
            await conn.execute(
                select(literal_column("jsonb_typeof(metadata_json->'view')"))
                .select_from(AgentRuns)
                .where(AgentRuns.id == run_id)
            )
        ).scalar_one()
        await trans.rollback()
    await eng.dispose()
    assert t == "object"
