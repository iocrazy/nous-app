"""Integration tests for MediaRepositoryOrm (Task 5.1) against real PG.

The media repository runs on the SQLAlchemy 2.0 ORM session layer now —
``read_scope()`` for reads, ``write_scope()`` (which COMMITS) for writes.
These tests run real SQL against a real Postgres to prove:

  - The 12 migrated methods keep their exact dict return shapes (the swap
    must be invisible to the 65 callers).
  - **THE P0 REGRESSION**: ``update()`` actually PERSISTS. The old asyncpg
    path ran the UPDATE on a bare ``connect()`` (no txn) and silently
    rolled back on close, so a fresh read saw the OLD value. The
    ``test_update_persists_after_commit_*`` tests below open a SEPARATE
    fresh connection (and a fresh repo read) and confirm the new value is
    there — this would FAIL on the silent-rollback path.
  - The ``resource_id`` overlay shape in get_user_media_list / search.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub
schema. Skips otherwise. Use the dev stack for these write-heavy tests:

    source /tmp/orm2_integration.env  # sets INTEGRATION_DATABASE_URL
    uv run pytest tests/integration/test_media_repository_orm.py -v

Rows are inserted with platform_id prefix ``__test_orm_media_`` for
predictable cleanup; the fixture deletes them after each test even on
failure.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PLATFORM_PREFIX = "__test_orm_media_"


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine + sessionmaker at the test DSN.

    The ORM session scopes build a sessionmaker bound to get_engine(), so
    we must reset BOTH singletons (engine + sessionmaker) before and after,
    and override the DSN, so read_scope()/write_scope() hit the test DB."""
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
    """Delete any parsed_media row (cascading resources) whose platform_id
    starts with the test prefix. Runs even on failure."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM resources WHERE media_id IN ("
            "SELECT id FROM parsed_media WHERE platform_id LIKE $1)",
            _PLATFORM_PREFIX + "%",
        )
        await conn.execute(
            "DELETE FROM parsed_media WHERE platform_id LIKE $1",
            _PLATFORM_PREFIX + "%",
        )
    finally:
        await conn.close()


@asynccontextmanager
async def _seed_parsed_media(
    integration_db_url: str, **overrides
) -> AsyncIterator[dict]:
    """Insert one parsed_media row via a raw asyncpg connection (NOT the
    repo — so reads/writes under test are exercised independently) and
    yield it as a dict."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        platform_id = f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:12]}"
        defaults = {
            "platform_id": platform_id,
            "source_platform": "douyin",
            "title": "ORM integration test row",
            "original_url": f"https://test.example/{platform_id}",
        }
        defaults.update(overrides)
        cols = list(defaults.keys())
        vals = list(defaults.values())
        placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
        col_list = ", ".join(f'"{c}"' for c in cols)
        row = await conn.fetchrow(
            f"INSERT INTO parsed_media ({col_list}) VALUES ({placeholders}) "
            f"RETURNING *",
            *vals,
        )
        yield dict(row)
    finally:
        await conn.close()


async def _fetch_title_raw(integration_db_url: str, platform_id: str):
    """Read a row's title via a fresh, independent asyncpg connection —
    used to prove a write PERSISTED past the repo's own commit boundary."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        return await conn.fetchval(
            "SELECT title FROM parsed_media WHERE platform_id = $1", platform_id
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.media_repository_orm import MediaRepositoryOrm

    return MediaRepositoryOrm()


# ─── Reads ─────────────────────────────────────────────────────────────


async def test_get_by_platform_id_finds_seeded_row(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(integration_db_url) as seeded:
        result = await _repo().get_by_platform_id(seeded["platform_id"])
        assert result is not None
        assert result["platform_id"] == seeded["platform_id"]
        assert result["title"] == "ORM integration test row"
        # SELECT * parity: the metadata column must surface under its DB
        # name, never as the model's ``metadata_`` attribute.
        assert "metadata" in result
        assert "metadata_" not in result


async def test_read_dict_boundary_returns_plain_python_types(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Parity the ``==`` tests can't catch: the read dict must carry BARE
    python types, not ORM-typed objects.

    The ORM types the 5 ``*_download_status`` columns as ``Enum(DownloadStatus)``,
    so a naive read leaks ``DownloadStatus`` members. Members are str
    subclasses → ``== "completed"`` passes, but ``str(x)`` yields
    ``"DownloadStatus.COMPLETED"`` ≠ ``"completed"`` (breaks the 65 callers).
    And the JSONB ``metadata`` column maps to the attribute ``metadata_``; a
    naive ``getattr(obj, "metadata")`` would leak the SQLAlchemy MetaData
    registry object instead of the row value. Lock both."""
    async with _seed_parsed_media(
        integration_db_url, video_download_status="completed"
    ) as seeded:
        repo = _repo()

        # Full-row read (get_by_platform_id → _orm_obj_to_dict).
        full = await repo.get_by_platform_id(seeded["platform_id"])
        assert full is not None
        status = full["video_download_status"]
        assert type(status) is str, (
            f"status leaked {type(status).__name__}, expected bare str — "
            f"enum-vs-string read parity drift"
        )
        assert status == "completed"
        assert str(status) == "completed"  # f-string parity — fails for enum
        # metadata must be the JSONB value (dict / None), never MetaData.
        assert type(full["metadata"]).__name__ != "MetaData"
        assert full["metadata"] is None or isinstance(full["metadata"], dict)

        # Card read (get_all → _pm_card_dict) — also carries status columns.
        rows = await repo.get_all(limit=500)
        card = next(
            (r for r in rows if int(r.get("id") or 0) == int(seeded["id"])), None
        )
        assert card is not None
        assert type(card["video_download_status"]) is str
        assert str(card["video_download_status"]) == "completed"


async def test_get_by_platform_id_returns_none_for_missing(
    integration_db_url, patched_engine
):
    result = await _repo().get_by_platform_id(f"{_PLATFORM_PREFIX}missing_zzz")
    assert result is None


async def test_get_by_id_with_str_input_against_bigint_column(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """parsed_media.id is BIGINT. API path params arrive as str; the repo
    must _bigint-coerce before binding."""
    async with _seed_parsed_media(integration_db_url) as seeded:
        result = await _repo().get_by_id(str(seeded["id"]))
        assert result is not None, "get_by_id failed with str input vs bigint"
        assert int(result["id"]) == seeded["id"]


async def test_get_downloaded_by_platform_id_filters_by_status(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(
        integration_db_url, video_download_status="pending"
    ) as seeded:
        repo = _repo()
        # Pending → no result
        result = await repo.get_downloaded_by_platform_id(seeded["platform_id"])
        assert result is None

        conn = await asyncpg.connect(integration_db_url)
        try:
            await conn.execute(
                "UPDATE parsed_media SET video_download_status='completed', "
                "download_path='/test/path.mp4' WHERE id = $1",
                seeded["id"],
            )
        finally:
            await conn.close()

        result = await repo.get_downloaded_by_platform_id(seeded["platform_id"])
        assert result is not None
        assert result["download_path"] == "/test/path.mp4"
        # Projection parity: only the 6 dedup columns.
        assert set(result.keys()) == {
            "id",
            "download_path",
            "storage_size",
            "cover_download_path",
            "source_platform",
            "platform_id",
        }


async def test_get_pending_downloads_global_and_user_scoped(
    integration_db_url, patched_engine, cleanup_test_rows
):
    from app.core.enums import DownloadStatus

    async with _seed_parsed_media(
        integration_db_url, video_download_status="pending"
    ) as media:
        repo = _repo()
        # Global: our seeded pending row is present.
        rows = await repo.get_pending_downloads(
            status=DownloadStatus.PENDING, limit=500
        )
        assert any(int(r["id"]) == int(media["id"]) for r in rows)

        conn = await asyncpg.connect(integration_db_url)
        try:
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows for creator_id")
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', 'pending_test.mp4')",
                test_user_id,
                media["id"],
            )
        finally:
            await conn.close()

        user_rows = await repo.get_pending_downloads(
            status=DownloadStatus.PENDING, limit=500, user_id=str(test_user_id)
        )
        assert any(int(r["id"]) == int(media["id"]) for r in user_rows)


async def test_get_all_returns_card_projection(
    integration_db_url, patched_engine, cleanup_test_rows
):
    from app.repositories.media_repository import MediaRepository

    card_cols = {c.strip() for c in MediaRepository.CARD_SELECT.split(",")}
    async with _seed_parsed_media(integration_db_url) as media:
        rows = await _repo().get_all(limit=500)
        match = next(
            (r for r in rows if int(r.get("id") or 0) == int(media["id"])), None
        )
        assert match is not None
        # CARD projection only — no heavy AI text blobs.
        assert set(match.keys()) == card_cols
        assert "ai_extract_text" not in match


async def test_get_user_media_list_overlays_resource_id(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows for creator_id")
            resource = await conn.fetchrow(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', 'list_test.mp4') RETURNING *",
                test_user_id,
                media["id"],
            )
        finally:
            await conn.close()

        rows = await _repo().get_user_media_list(str(test_user_id), limit=500)
        match = next(
            (r for r in rows if int(r.get("id") or 0) == int(media["id"])), None
        )
        assert match is not None, "seeded media not in user_media_list"
        assert match.get("title") == "ORM integration test row"
        # resource_id overlaid; the internal alias must never leak.
        assert int(match["resource_id"]) == resource["id"]
        assert "__resource_id" not in match


async def test_search_filters_by_keyword(
    integration_db_url, patched_engine, cleanup_test_rows
):
    unique_kw = f"kwprobe_{uuid.uuid4().hex[:8]}"
    async with _seed_parsed_media(
        integration_db_url, title=f"prefix {unique_kw} suffix"
    ) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows for creator_id")
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', 'search_test.mp4')",
                test_user_id,
                media["id"],
            )
        finally:
            await conn.close()

        rows = await _repo().search(str(test_user_id), keyword=unique_kw, limit=10)
        assert len(rows) >= 1, "keyword filter missed seeded title"
        match = next(
            (r for r in rows if int(r.get("id") or 0) == int(media["id"])), None
        )
        assert match is not None
        assert "resource_id" in match
        assert "__resource_id" not in match


async def test_get_statistics_shape_and_counts(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(
        integration_db_url, video_download_status="completed", datasize_bytes=1024
    ) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows for creator_id")
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', 'stats_test.mp4')",
                test_user_id,
                media["id"],
            )
        finally:
            await conn.close()

        stats = await _repo().get_statistics(str(test_user_id))
        for key in (
            "total",
            "pending",
            "completed",
            "failed",
            "skipped",
            "total_storage_bytes",
            "unique_authors",
        ):
            assert key in stats, f"missing key: {key}"
            assert isinstance(stats[key], int), f"{key} should be int"
        assert stats["skipped"] >= 0
        assert stats["total"] >= 1
        assert stats["completed"] >= 1
        assert stats["total_storage_bytes"] >= 1024


# ─── Writes (committing — fixes the P0) ─────────────────────────────────


async def test_create_persists(integration_db_url, patched_engine, cleanup_test_rows):
    """INSERT via write_scope must COMMIT. Verify with a fresh read."""
    platform_id = f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:12]}"
    created = await _repo().create(
        {
            "platform_id": platform_id,
            "source_platform": "douyin",
            "title": "Created via ORM",
            "original_url": f"https://test.example/{platform_id}",
        }
    )
    assert created["platform_id"] == platform_id
    # Fresh independent read confirms the row is actually committed.
    title = await _fetch_title_raw(integration_db_url, platform_id)
    assert title == "Created via ORM"


async def test_update_returns_updated_row(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(integration_db_url) as seeded:
        result = await _repo().update(seeded["platform_id"], {"title": "Updated title"})
        assert result is not None
        assert result["title"] == "Updated title"
        assert result["updated_at"] >= seeded["updated_at"]


async def test_update_persists_after_commit_P0_REGRESSION(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """THE P0 REGRESSION TEST.

    The old asyncpg path ran ``fetch_one("UPDATE … RETURNING *")`` on a bare
    ``engine.connect()`` (no transaction). The RETURNING row looked updated,
    but the UPDATE silently ROLLED BACK on connection close — a fresh read
    saw the OLD value (silent data loss). This test:

      1. updates the title via the repo,
      2. opens a SEPARATE fresh connection (a different session/txn entirely),
      3. asserts the new value is there.

    On the silent-rollback path step 3 fails (still the old title). On the
    write_scope()-committing ORM path it passes."""
    async with _seed_parsed_media(integration_db_url) as seeded:
        new_title = f"persisted_{uuid.uuid4().hex[:8]}"
        await _repo().update(seeded["platform_id"], {"title": new_title})

        # Fresh, independent connection — proves the commit, not a buffer.
        persisted = await _fetch_title_raw(integration_db_url, seeded["platform_id"])
        assert persisted == new_title, (
            "UPDATE did not persist — silent-rollback P0 is back. The write "
            "must go through write_scope() (which commits), not a bare "
            "connect()."
        )

        # And a fresh repo read (its own read_scope session) agrees.
        reread = await _repo().get_by_platform_id(seeded["platform_id"])
        assert reread is not None
        assert reread["title"] == new_title


async def test_delete_persists(integration_db_url, patched_engine, cleanup_test_rows):
    async with _seed_parsed_media(integration_db_url) as seeded:
        ok = await _repo().delete(seeded["platform_id"])
        assert ok is True
        # Fresh read confirms the delete committed.
        title = await _fetch_title_raw(integration_db_url, seeded["platform_id"])
        assert title is None


async def test_mark_stale_downloads_failed_skips_recent(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """A row whose updated_at is INSIDE the window must NOT be flipped."""
    async with _seed_parsed_media(
        integration_db_url, video_download_status="downloading"
    ) as media:
        repo = _repo()
        await repo.mark_stale_downloads_failed(timeout_minutes=30)
        after = await repo.get_by_id(str(media["id"]))
        assert after is not None
        assert after["video_download_status"] == "downloading"


async def test_mark_stale_downloads_failed_flips_old_and_persists(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """A row stuck 'downloading' past the cutoff must flip to 'failed' AND
    the change must persist (committing write_scope, not silent rollback)."""
    async with _seed_parsed_media(
        integration_db_url, video_download_status="downloading"
    ) as media:
        # Backdate updated_at past the cutoff. parsed_media has a
        # BEFORE-UPDATE trigger (update_videos_updated_at) that resets
        # updated_at = now() on every UPDATE, so a plain backdate is
        # immediately overwritten. Disable the trigger for the backdate
        # so the row lands genuinely stale.
        conn = await asyncpg.connect(integration_db_url)
        try:
            await conn.execute(
                "ALTER TABLE parsed_media DISABLE TRIGGER update_videos_updated_at"
            )
            await conn.execute(
                "UPDATE parsed_media SET updated_at = now() - interval '2 hours' "
                "WHERE id = $1",
                media["id"],
            )
            await conn.execute(
                "ALTER TABLE parsed_media ENABLE TRIGGER update_videos_updated_at"
            )
        finally:
            await conn.close()

        count = await _repo().mark_stale_downloads_failed(timeout_minutes=30)
        assert count >= 1

        # Fresh independent read — the flip must be committed.
        conn = await asyncpg.connect(integration_db_url)
        try:
            status = await conn.fetchval(
                "SELECT video_download_status FROM parsed_media WHERE id = $1",
                media["id"],
            )
        finally:
            await conn.close()
        assert str(status) == "failed"
