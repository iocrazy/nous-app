"""Integration tests for the ORM AdminStatsRepository (Phase 2 admin wave) vs real PG.

Proves the REST → ORM swap is invisible AND strategy-C parity for the admin
dashboard aggregations:

  - COUNT(*) / distinct count → native int (the 5.3 trap)

(The growth / video-status / per-user storage reads and their tests were
deleted with the three unused ``/admin/stats`` routes they served, P8.)

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_stats_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_stats_"


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
    seeded_media: list[int] = []
    yield seeded_media
    conn = await asyncpg.connect(integration_db_url)
    try:
        if seeded_media:
            await conn.execute(
                "DELETE FROM parsed_media WHERE id = ANY($1::bigint[])", seeded_media
            )
        await conn.execute("DELETE FROM user_logs WHERE action LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy FK")
    return uid


async def _seed_media(conn, *, status="completed", created_at=None) -> int:
    created_at = created_at or datetime.now(timezone.utc)
    return await conn.fetchval(
        "INSERT INTO parsed_media (platform_id, source_platform, original_url, "
        "video_download_status, created_at) VALUES ($1, 'test', $2, "
        "$3::download_status, $4) RETURNING id",
        f"{_PREFIX}{uuid.uuid4().hex[:10]}",
        "https://example.test/x",
        status,
        created_at,
    )


def _repo():
    from app.repositories.admin.stats_repository import AdminStatsRepository

    return AdminStatsRepository()


async def test_counts_return_native_int(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        seeded = cleanup_test_rows
        seeded.append(await _seed_media(conn, status="completed"))
        seeded.append(await _seed_media(conn, status="failed"))
    finally:
        await conn.close()

    repo = _repo()
    total_media = await repo.count_parsed_media()
    completed = await repo.count_parsed_media(video_download_status="completed")
    total_users = await repo.count_user_profiles()
    total_teams = await repo.count_teams()
    for v in (total_media, completed, total_users, total_teams):
        assert type(v) is int
    assert total_media >= 2
    assert completed >= 1


async def test_distinct_active_users_native_int(
    integration_db_url, patched_engine, cleanup_test_rows
):
    since = datetime.now(timezone.utc) - timedelta(days=1)
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        for _ in range(3):
            await conn.execute(
                "INSERT INTO user_logs (user_id, action, message, created_at) "
                "VALUES ($1, $2, 'm', $3)",
                user_id,
                f"{_PREFIX}{uuid.uuid4().hex[:6]}",
                datetime.now(timezone.utc),
            )
    finally:
        await conn.close()

    count = await _repo().distinct_active_users_since(since)
    assert type(count) is int
    assert count >= 1  # 3 logs for ONE user → distinct == 1 (at least)


# ─── Factory (ORM-only, post-rollout) ───────────────────────────────────


async def test_factory_returns_orm_repository():
    """Per-domain rollout flag retired → factory unconditionally returns the
    ORM-backed AdminStatsRepository."""
    from app.repositories.admin import stats_repository as mod

    assert type(mod.get_admin_stats_repository()) is mod.AdminStatsRepository
