"""The cross-process cancel channel, end to end on real Postgres.

Task Center cancel runs in ``nous-backend``; the child runs in
``nous-worker``; the per-process registry cannot bridge them (fh4 H2). The
bridge is ``task_tracking``: the backend's ``UnifiedTaskManager.cancel``
writes ``phase``/``status = 'cancelled'`` and the worker's ``run_process``
polls it via ``is_workflow_cancelled``. The unit tests stub the session;
this runs the REAL writer and the REAL reader against the schema.

The fixture row is committed (the app engine is a different connection from
any test transaction) and deleted in teardown.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url: str):
    import app.db.engine as db_engine
    import app.db.session as db_session

    db_engine._engine = None
    db_session._sessionmaker = None
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    try:
        await db_engine.dispose_engine()
    finally:
        db_engine._engine = None
        db_session._sessionmaker = None


@pytest.fixture
async def tracked_row(integration_db_url: str):
    """A processing workflow row, committed; removed afterwards."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        integration_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    wf = f"fh4-t1-{uuid.uuid4().hex[:12]}"
    async with engine.begin() as c:
        user_id = (
            await c.execute(text("SELECT id::text FROM auth.users LIMIT 1"))
        ).scalar()
        if user_id is None:
            await engine.dispose()
            pytest.skip("drift DB has no auth.users row for the task_tracking FK")
        await c.execute(
            text(
                "INSERT INTO task_tracking (user_id, task_type, dbos_workflow_id,"
                " title, phase, status) VALUES (:u, 'download', :wf, 'fh4 probe',"
                " 'processing', 'processing')"
            ),
            {"u": user_id, "wf": wf},
        )
    try:
        yield wf, user_id
    finally:
        async with engine.begin() as c:
            await c.execute(
                text("DELETE FROM task_tracking WHERE dbos_workflow_id = :wf"),
                {"wf": wf},
            )
        await engine.dispose()


async def test_task_center_cancel_is_what_the_worker_poll_sees(
    patched_engine, tracked_row
):
    from app.agent_framework.process_runner import is_workflow_cancelled
    from app.services.infra.unified_task_manager import get_task_manager

    wf, user_id = tracked_row
    assert await is_workflow_cancelled(wf) is False
    await get_task_manager().cancel(wf, user_id)
    assert await is_workflow_cancelled(wf) is True
    assert await is_workflow_cancelled(f"{wf}-absent") is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
async def test_cancel_from_api_process_reaches_worker_child(
    patched_engine, tracked_row
):
    from app.agent_framework.process_runner import run_process
    from app.services.infra.unified_task_manager import get_task_manager

    wf, user_id = tracked_row

    async def cancel_later():
        await asyncio.sleep(0.5)
        await get_task_manager().cancel(wf, user_id)

    canceller = asyncio.create_task(cancel_later())
    res = await asyncio.wait_for(
        run_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout_s=30,
            grace_s=1.0,
            workflow_id=wf,
            cancel_poll_s=0.2,
        ),
        timeout=10,
    )
    await canceller
    assert res.cancelled is True
    assert res.timed_out is False
    assert res.signal == signal.SIGTERM
