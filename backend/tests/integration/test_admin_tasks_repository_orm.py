"""Integration tests for AdminTasksRepositoryOrm (Phase 2 admin wave) vs real PG.

task_tracking admin Task Center reads + cancel/retry writes.

Proves REST → ORM swap invisibility + strategy-C parity:
  - user_id (uuid) → STR (DICT-KEY trap)
  - created_at / started_at / completed_at (timestamptz) → ISO STR
  - metadata (renamed metadata_) → native dict keyed as "metadata"
  - progress / speed / total_bytes / cost_cents / COUNT → native int
  - or_ search (incl. metadata->>original_url ilike + digit media_id eq)
  - update() WRITES verbatim (incl. trigger-owned columns) and COMMITS

Seeded rows use a synthetic dbos_workflow_id prefix and task_kind='agent_task'
(so a direct status write is legitimate for the seed; the repo's update path is
tested separately for the write contract).

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_tasks_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_admtask_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
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
async def cleanup_test_rows(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM task_tracking WHERE dbos_workflow_id LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


@pytest.fixture
async def seed_user(integration_db_url) -> str:
    """A real auth.users id — task_tracking.user_id has an FK to users. Skip if
    the DB has no users to borrow."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    finally:
        await conn.close()
    if uid is None:
        pytest.skip("no auth.users row to satisfy task_tracking.user_id FK")
    return str(uid)


async def _seed(conn, seed_user, **overrides):
    payload = {
        "dbos_workflow_id": f"{_PREFIX}{uuid.uuid4().hex}",
        "user_id": seed_user,
        "task_type": "download",
        "status": "completed",
        "phase": "completed",
        "title": "Test Task",
        "task_kind": "agent_task",
        "created_at": datetime.now(timezone.utc),
    }
    payload.update(overrides)
    cols = list(payload.keys())
    ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    await conn.execute(
        f"INSERT INTO task_tracking ({col_list}) VALUES ({ph})", *payload.values()
    )
    return payload["dbos_workflow_id"]


def _repo():
    from app.repositories.admin.tasks_repository_orm import AdminTasksRepositoryOrm

    return AdminTasksRepositoryOrm()


async def test_list_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows, seed_user
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        wid = await _seed(
            conn,
            seed_user,
            progress=42,
            speed=1000,
            total_bytes=500000,
            cost_cents=7,
            metadata='{"original_url": "https://example.com/x"}',
            title=f"{_PREFIX}title",
        )
    finally:
        await conn.close()

    rows, total = await _repo().list(page=1, page_size=200, search=f"{_PREFIX}title")
    ours = [r for r in rows if r["dbos_workflow_id"] == wid]
    assert ours and total >= 1
    r = ours[0]
    assert type(r["user_id"]) is str  # uuid → str (dict-key trap)
    assert type(r["created_at"]) is str
    assert datetime.fromisoformat(r["created_at"])
    assert type(r["progress"]) is int and r["progress"] == 42
    assert type(r["total_bytes"]) is int and r["total_bytes"] == 500000
    assert type(r["cost_cents"]) is int and r["cost_cents"] == 7
    # renamed metadata_ keyed back as "metadata"
    assert r["metadata"] == {"original_url": "https://example.com/x"}
    # status / phase are plain strings (NOT Enum members)
    assert type(r["status"]) is str
    # exact projection keys
    assert set(r.keys()) == {
        "dbos_workflow_id",
        "user_id",
        "task_type",
        "status",
        "phase",
        "title",
        "subtitle",
        "progress",
        "speed",
        "total_bytes",
        "error_msg",
        "error_code",
        "resource_id",
        "media_id",
        "cost_cents",
        "metadata",
        "created_at",
        "started_at",
        "completed_at",
    }


async def test_search_metadata_original_url(
    integration_db_url, patched_engine, cleanup_test_rows, seed_user
):
    marker = uuid.uuid4().hex
    conn = await asyncpg.connect(integration_db_url)
    try:
        wid = await _seed(
            conn, seed_user, metadata=f'{{"original_url": "https://site/{marker}"}}'
        )
        await _seed(conn, seed_user, metadata='{"original_url": "https://site/other"}')
    finally:
        await conn.close()

    rows, _ = await _repo().list(page=1, page_size=200, search=marker)
    wids = {r["dbos_workflow_id"] for r in rows}
    assert wid in wids


async def test_search_digit_media_id(
    integration_db_url, patched_engine, cleanup_test_rows, seed_user
):
    digits = str(uuid.uuid4().int)[:15]
    conn = await asyncpg.connect(integration_db_url)
    try:
        wid = await _seed(conn, seed_user, media_id=digits)
    finally:
        await conn.close()

    rows, _ = await _repo().list(page=1, page_size=200, search=digits)
    assert wid in {r["dbos_workflow_id"] for r in rows}


async def test_count_total_and_by_status_native_int(
    integration_db_url, patched_engine, cleanup_test_rows, seed_user
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed(conn, seed_user, status="failed")
    finally:
        await conn.close()

    total = await _repo().count_total()
    failed = await _repo().count_by_status("failed")
    assert type(total) is int and total >= 1
    assert type(failed) is int and failed >= 1


async def test_get_and_update_round_trip(
    integration_db_url, patched_engine, cleanup_test_rows, seed_user
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        wid = await _seed(conn, seed_user, status="failed", phase="failed")
    finally:
        await conn.close()

    got = await _repo().get(wid)
    assert got is not None and got["status"] == "failed"

    # Reproduces the legacy admin retry write verbatim — incl. trigger-owned cols.
    await _repo().update(
        wid,
        {
            "status": "pending",
            "phase": "queued",
            "progress": 0,
            "error_msg": None,
            "error_code": None,
            "started_at": None,
            "completed_at": None,
        },
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow(
            "SELECT status, phase, progress FROM task_tracking "
            "WHERE dbos_workflow_id = $1",
            wid,
        )
    finally:
        await conn.close()
    assert row["status"] == "pending" and row["phase"] == "queued"
    assert row["progress"] == 0


async def test_get_absent_returns_none(integration_db_url, patched_engine):
    assert await _repo().get(f"{_PREFIX}does-not-exist-{uuid.uuid4().hex}") is None


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import tasks_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TASKS", False)
    assert type(mod.get_admin_tasks_repository()) is mod.AdminTasksRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import tasks_repository as mod
    from app.repositories.admin.tasks_repository_orm import AdminTasksRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TASKS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_admin_tasks_repository(), AdminTasksRepositoryOrm)
