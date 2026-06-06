"""Integration tests for AdminVideosRepositoryOrm (Phase 2 admin wave) vs real PG.

parsed_media admin video management — reads + delete/reset-for-retry writes.

Proves REST → ORM swap invisibility + strategy-C parity:
  - video/music/cover_download_status Enum(DownloadStatus) → bare .value STR
  - id / datasize_bytes / counts (int/bigint) → native int; COUNT / SUM → native int
  - created_at (timestamptz) → ISO STR
  - or_ search (title/platform_id ilike); sort validated vs ALLOWED_SORT_FIELDS
  - delete() / reset_for_retry() return bool and COMMIT

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_videos_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_admvid_"


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
            "DELETE FROM parsed_media WHERE platform_id LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


async def _seed(conn, **overrides):
    payload = {
        "platform_id": f"{_PREFIX}{uuid.uuid4().hex}",
        "original_url": "https://example.com/v",
        "source_platform": "douyin",
        "video_download_status": "completed",
        "datasize_bytes": 1000,
        "title": "Test Video",
        "created_at": datetime.now(timezone.utc),
    }
    payload.update(overrides)
    cols = list(payload.keys())
    ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    return await conn.fetchval(
        f"INSERT INTO parsed_media ({col_list}) VALUES ({ph}) RETURNING id",
        *payload.values(),
    )


def _repo():
    from app.repositories.admin.videos_repository_orm import AdminVideosRepositoryOrm

    return AdminVideosRepositoryOrm()


async def test_list_shape_and_enum_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    title = f"{_PREFIX}{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        vid = await _seed(
            conn, title=title, video_download_status="completed", datasize_bytes=2048
        )
    finally:
        await conn.close()

    rows, total = await _repo().list_with_filters(page=1, page_size=50, search=title)
    ours = [r for r in rows if r["id"] == vid]
    assert ours and total >= 1
    r = ours[0]
    assert type(r["id"]) is int
    # Enum → bare .value string (NOT "DownloadStatus.COMPLETED")
    assert r["video_download_status"] == "completed"
    assert type(r["video_download_status"]) is str
    assert type(r["datasize_bytes"]) is int and r["datasize_bytes"] == 2048
    assert type(r["created_at"]) is str
    assert datetime.fromisoformat(r["created_at"])


async def test_get_by_id_and_absent(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        vid = await _seed(conn)
    finally:
        await conn.close()

    row = await _repo().get_by_id(vid)
    assert row is not None and row["id"] == vid
    assert await _repo().get_by_id(-1) is None


async def test_count_and_sum_native_int(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed(conn, video_download_status="failed", datasize_bytes=4096)
    finally:
        await conn.close()

    total = await _repo().count_total()
    failed = await _repo().count_by_status("failed")
    storage = await _repo().sum_storage_bytes()
    assert type(total) is int and total >= 1
    assert type(failed) is int and failed >= 1
    assert type(storage) is int and storage >= 4096

    counts = await _repo().counts_by_statuses(["completed", "failed"])
    assert set(counts.keys()) == {"completed", "failed"}
    assert all(type(v) is int for v in counts.values())


async def test_delete_commits_and_returns_bool(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        vid = await _seed(conn)
    finally:
        await conn.close()

    assert await _repo().delete(vid) is True
    assert await _repo().delete(vid) is False  # already gone

    conn = await asyncpg.connect(integration_db_url)
    try:
        exists = await conn.fetchval(
            "SELECT count(*) FROM parsed_media WHERE id = $1", vid
        )
    finally:
        await conn.close()
    assert exists == 0


async def test_reset_for_retry_commits(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        vid = await _seed(conn, video_download_status="failed", error_message="boom")
    finally:
        await conn.close()

    assert await _repo().reset_for_retry(vid) is True
    assert await _repo().reset_for_retry(-1) is False

    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow(
            "SELECT video_download_status, error_message FROM parsed_media "
            "WHERE id = $1",
            vid,
        )
    finally:
        await conn.close()
    assert str(row["video_download_status"]) == "pending"
    assert row["error_message"] is None


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import videos_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_VIDEOS", False)
    assert type(mod.get_admin_videos_repository()) is mod.AdminVideosRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import videos_repository as mod
    from app.repositories.admin.videos_repository_orm import AdminVideosRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_VIDEOS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_admin_videos_repository(), AdminVideosRepositoryOrm)
