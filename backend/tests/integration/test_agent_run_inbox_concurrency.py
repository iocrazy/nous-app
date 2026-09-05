"""Two claimers racing for one target get disjoint rows (SKIP LOCKED).

Requires INTEGRATION_DATABASE_URL (a PG with mig 453 applied); skipped otherwise.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import uuid

import pytest

pytestmark = pytest.mark.integration
DSN = os.environ.get("INTEGRATION_DATABASE_URL")


@pytest.mark.skipif(not DSN, reason="INTEGRATION_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_concurrent_claims_are_disjoint():
    import asyncpg
    from sqlalchemy.dialects import postgresql

    from app.repositories.agent_run_inbox_repository import claim_stmt

    conn = await asyncpg.connect(DSN)
    target = int(uuid.uuid4().int % 10**12)
    try:
        for i in range(6):
            await conn.execute(
                "INSERT INTO agent_run_inbox(target_kind,target_id,user_id,kind,content)"
                " VALUES('issue',$1,gen_random_uuid(),'steer',$2::jsonb)",
                target,
                '{"body":"%d"}' % i,
            )
        now = dt.datetime.now(dt.timezone.utc)

        def _sql(run_id):
            c = claim_stmt([("issue", target)], run_id, 1, 1, now).compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
            return str(c)

        a, b = await asyncpg.connect(DSN), await asyncpg.connect(DSN)
        try:
            ra, rb = await asyncio.gather(a.fetch(_sql(1)), b.fetch(_sql(2)))
        finally:
            await a.close()
            await b.close()
        ids_a = {r["id"] for r in ra}
        ids_b = {r["id"] for r in rb}
        assert ids_a.isdisjoint(ids_b)
        assert len(ids_a | ids_b) == 6
    finally:
        await conn.execute("DELETE FROM agent_run_inbox WHERE target_id=$1", target)
        await conn.close()
