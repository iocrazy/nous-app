"""Migration 508 on real Postgres: a cancelled task stays cancelled.

The Task Center cancel writes ``task_tracking`` directly and does not cancel
the DBOS workflow. Since fh4 T1 the worker then kills the child, the step
fails, and the workflow ends in ERROR. Before 508 the lifecycle mirror turned
that into ``failed``; this drives the REAL trigger function through a REAL
``dbos.workflow_status`` update to prove it no longer does — and that a row
that was never cancelled still becomes ``failed``.

Everything runs in one transaction that is rolled back (the migration, the
trigger binding the drift DB lacks, and the rows), so nothing persists.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
if not _TEST_DSN:
    pytest.skip("INTEGRATION_DATABASE_URL not set", allow_module_level=True)

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "508_task_tracking_cancelled_sticky.sql"
)


@pytest.fixture
async def conn():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        _TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    async with engine.connect() as c:
        tx = await c.begin()
        try:
            if os.environ.get("FH4_SKIP_508") != "1":  # RED demo switch
                await c.exec_driver_sql(_MIGRATION.read_text(encoding="utf-8"))
            await c.execute(
                text(
                    "DROP TRIGGER IF EXISTS trg_mirror_dbos_lifecycle"
                    " ON dbos.workflow_status"
                )
            )
            await c.execute(
                text(
                    "CREATE TRIGGER trg_mirror_dbos_lifecycle AFTER INSERT OR UPDATE"
                    " OF status, error, started_at_epoch_ms, updated_at"
                    " ON dbos.workflow_status FOR EACH ROW"
                    " EXECUTE FUNCTION public.mirror_dbos_lifecycle_to_tracking()"
                )
            )
            yield c
        finally:
            await tx.rollback()
    await engine.dispose()


async def _tracked(conn, phase: str, status: str) -> str:
    from sqlalchemy import text

    user_id = (await conn.execute(text("SELECT id FROM auth.users LIMIT 1"))).scalar()
    if user_id is None:
        pytest.skip("drift DB has no auth.users row for the task_tracking FK")
    wf = f"fh4-508-{uuid.uuid4().hex[:12]}"
    # DBOS row first, so its insert mirror finds no task_tracking row yet and
    # the row starts in exactly the state under test.
    await conn.execute(
        text(
            "INSERT INTO dbos.workflow_status (workflow_uuid, status, created_at,"
            " updated_at) VALUES (:wf, 'RUNNING', 1, 1)"
        ),
        {"wf": wf},
    )
    await conn.execute(
        text(
            "INSERT INTO task_tracking (user_id, task_type, dbos_workflow_id, title,"
            " phase, status) VALUES (:u, 'download', :wf, 'fh4 508', :p, :s)"
        ),
        {"u": user_id, "wf": wf, "p": phase, "s": status},
    )
    return wf


async def _dbos_ends(conn, wf: str, status: str) -> tuple[str, str]:
    from sqlalchemy import text

    await conn.execute(
        text(
            "UPDATE dbos.workflow_status SET status = :s, updated_at = 2"
            " WHERE workflow_uuid = :wf"
        ),
        {"s": status, "wf": wf},
    )
    row = (
        await conn.execute(
            text(
                "SELECT phase, status FROM task_tracking WHERE dbos_workflow_id = :wf"
            ),
            {"wf": wf},
        )
    ).one()
    return row.phase, row.status


async def test_cancelled_row_survives_a_later_error_mirror(conn):
    from sqlalchemy import text

    wf = await _tracked(conn, "in_progress", "processing")
    # the Task Center cancel (direct write, DBOS workflow untouched)
    await conn.execute(
        text(
            "UPDATE task_tracking SET phase='cancelled', status='cancelled'"
            " WHERE dbos_workflow_id = :wf"
        ),
        {"wf": wf},
    )
    assert await _dbos_ends(conn, wf, "ERROR") == ("cancelled", "cancelled")
    assert await _dbos_ends(conn, wf, "RETRIES_EXCEEDED") == ("cancelled", "cancelled")


async def test_uncancelled_row_still_becomes_failed(conn):
    wf = await _tracked(conn, "in_progress", "processing")
    assert await _dbos_ends(conn, wf, "ERROR") == ("failed", "failed")


async def test_failed_row_still_not_regressed_by_success(conn):
    wf = await _tracked(conn, "failed", "failed")
    assert await _dbos_ends(conn, wf, "SUCCESS") == ("failed", "failed")


async def test_plain_success_still_completes(conn):
    wf = await _tracked(conn, "in_progress", "processing")
    assert await _dbos_ends(conn, wf, "SUCCESS") == ("completed", "completed")
