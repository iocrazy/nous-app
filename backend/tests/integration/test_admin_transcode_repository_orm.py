"""Integration tests for AdminTranscodeRepositoryOrm (Phase 2 admin wave) vs real PG.

resource_versions / resources / parsed_media reads + system_settings read/upsert.

Proves REST → ORM swap invisibility + strategy-C parity:
  - ALL ids/FKs BIGINT → native int (5.3 trap); maps str() their keys
  - NO uuid selected in any path (defensive sweep is a no-op)
  - created_at / transcode_at (timestamptz) → ISO STR; transcode_status plain str
  - COUNT → native int; value (jsonb) → native dict
  - mime_type LIKE 'video/%' + status null/eq + min_size_mb filters reproduced
  - mark_pending UPDATE + upsert_setting ON CONFLICT WRITE + COMMIT
  - factory on/off

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_transcode_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_admtc_"


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
async def seed(integration_db_url):
    """Seed a resource + a video resource_version (failed status, >0 bytes). Yields
    ids. Cleans up after."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if uid is None:
            pytest.skip("no auth.users row for resources.creator_id")
        rid = await conn.fetchval(
            "INSERT INTO resources (creator_id, source_type, filename) "
            "VALUES ($1, 'web', $2) RETURNING id",
            uid,
            f"{_PREFIX}res",
        )
        vid = await conn.fetchval(
            "INSERT INTO resource_versions "
            "(resource_id, version_number, filename, file_size_bytes, mime_type, "
            " transcode_status) "
            "VALUES ($1, 1, $2, $3, 'video/mp4', 'failed') RETURNING id",
            rid,
            f"{_PREFIX}v.mp4",
            50 * 1024 * 1024,
        )
        yield {"resource_id": int(rid), "version_id": int(vid)}
    finally:
        await conn.execute("DELETE FROM resource_versions WHERE resource_id = $1", rid)
        await conn.execute("DELETE FROM resources WHERE id = $1", rid)
        await conn.close()


def _repo():
    from app.repositories.admin.transcode_repository_orm import (
        AdminTranscodeRepositoryOrm,
    )

    return AdminTranscodeRepositoryOrm()


async def test_list_versions_shape_and_parity(integration_db_url, patched_engine, seed):
    rows, total = await _repo().list_video_versions(
        page=1, page_size=200, status_filter="failed"
    )
    ours = [r for r in rows if int(r["id"]) == seed["version_id"]]
    assert ours and total >= 1
    r = ours[0]
    assert type(r["id"]) is int  # bigint → native int
    assert type(r["resource_id"]) is int
    assert type(r["file_size_bytes"]) is int
    assert type(r["created_at"]) is str  # timestamptz → ISO str
    assert r["transcode_status"] == "failed" and type(r["transcode_status"]) is str
    # exact LIST_COLUMNS projection keys
    assert set(r.keys()) == {
        "id",
        "resource_id",
        "version_number",
        "filename",
        "file_size_bytes",
        "mime_type",
        "transcode_status",
        "hls_path",
        "transcode_at",
        "created_at",
    }


async def test_min_size_filter(integration_db_url, patched_engine, seed):
    # our seed is 50MB; a 100MB floor excludes it, a 10MB floor includes it
    rows_hi, _ = await _repo().list_video_versions(
        page=1, page_size=200, min_size_mb=100
    )
    assert seed["version_id"] not in {int(r["id"]) for r in rows_hi}
    rows_lo, _ = await _repo().list_video_versions(
        page=1, page_size=200, min_size_mb=10
    )
    assert seed["version_id"] in {int(r["id"]) for r in rows_lo}


async def test_counts_native_int(integration_db_url, patched_engine, seed):
    total = await _repo().count_total_video_versions()
    failed = await _repo().count_by_status("failed")
    assert type(total) is int and total >= 1
    assert type(failed) is int and failed >= 1
    sc = await _repo().status_counts(["failed", "completed"])
    assert type(sc["failed"]) is int and sc["failed"] >= 1


async def test_resources_to_media_str_keyed(integration_db_url, patched_engine, seed):
    # link the seed resource to a parsed_media row
    conn = await asyncpg.connect(integration_db_url)
    try:
        mid = await conn.fetchval(
            "INSERT INTO parsed_media (source_platform, platform_id, title, original_url) "
            "VALUES ('test', $1, $2, $3) RETURNING id",
            f"{_PREFIX}{uuid.uuid4().hex[:8]}",
            f"{_PREFIX}media",
            f"https://example.com/{_PREFIX}",
        )
        await conn.execute(
            "UPDATE resources SET media_id = $1 WHERE id = $2", mid, seed["resource_id"]
        )
    finally:
        await conn.close()

    try:
        m = await _repo().resources_to_media([str(seed["resource_id"])])
        assert m.get(str(seed["resource_id"])) == str(mid)
        info = await _repo().media_info_bulk([str(mid)])
        assert str(mid) in info
        assert info[str(mid)]["title"] == f"{_PREFIX}media"
        assert type(info[str(mid)]["id"]) is int
    finally:
        conn = await asyncpg.connect(integration_db_url)
        try:
            await conn.execute(
                "UPDATE resources SET media_id = NULL WHERE id = $1",
                seed["resource_id"],
            )
            await conn.execute("DELETE FROM parsed_media WHERE id = $1", mid)
        finally:
            await conn.close()


async def test_get_version_and_batch(integration_db_url, patched_engine, seed):
    v = await _repo().get_version(seed["version_id"])
    assert v is not None
    assert set(v.keys()) == {"id", "resource_id", "mime_type"}
    assert type(v["id"]) is int and v["id"] == seed["version_id"]
    assert v["mime_type"] == "video/mp4"

    batch = await _repo().list_versions_for_batch("retry_failed")
    assert seed["version_id"] in {int(b["id"]) for b in batch}


async def test_get_version_absent_none(integration_db_url, patched_engine):
    assert await _repo().get_version(999999999999999999) is None


async def test_mark_pending_commit(integration_db_url, patched_engine, seed):
    await _repo().mark_pending(seed["version_id"])
    conn = await asyncpg.connect(integration_db_url)
    try:
        st = await conn.fetchval(
            "SELECT transcode_status FROM resource_versions WHERE id = $1",
            seed["version_id"],
        )
        assert st == "pending"  # committed
    finally:
        await conn.close()


async def test_settings_upsert_and_load(integration_db_url, patched_engine):
    key = f"transcode_{_PREFIX}{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    finally:
        await conn.close()
    try:
        await _repo().upsert_setting(key, {"enabled": True}, str(uid))
        settings_map = await _repo().load_settings()
        assert key in settings_map
        assert settings_map[key] == {"enabled": True}  # jsonb → native dict
        # upsert again (ON CONFLICT path)
        await _repo().upsert_setting(key, {"enabled": False}, str(uid))
        settings_map2 = await _repo().load_settings()
        assert settings_map2[key] == {"enabled": False}
    finally:
        conn = await asyncpg.connect(integration_db_url)
        try:
            await conn.execute("DELETE FROM system_settings WHERE key = $1", key)
        finally:
            await conn.close()


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import transcode_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TRANSCODE", False)
    assert type(mod.get_admin_transcode_repository()) is mod.AdminTranscodeRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import transcode_repository as mod
    from app.repositories.admin.transcode_repository_orm import (
        AdminTranscodeRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TRANSCODE", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_admin_transcode_repository(), AdminTranscodeRepositoryOrm)
