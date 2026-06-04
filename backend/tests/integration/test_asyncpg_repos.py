"""Integration tests for the asyncpg repository path.

Mock-only tests are insufficient for the supabase-py → asyncpg
migration: every Phase 3a-3e + Phase 4a self-review surfaced bugs
that mock tests didn't catch (str→bigint binding, ANY(::text[])
against bigint, row_to_json decoding, scalar coercion, ...). These
tests run real SQL against a real PG to catch the next class of
bugs BEFORE prod canary.

What's tested (vs unit/factory tests):
  - Real type binding: str id passed to bigint column actually
    works (or fails loud)
  - Real RETURNING clause shape (column names + values match)
  - Real JOIN behaviour for the L2 dedup probe
  - NULL handling in folder_id IS NULL paths
  - row_to_json decoding for embedded shapes

What's NOT tested here (canary's job):
  - Long-running connection stability (Bug C itself)
  - Concurrent pool exhaustion under real load
  - Trigger / RLS interaction with the asyncpg user

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the
mediahub schema (parsed_media / resources / etc.). Skips otherwise.
For local dev:

    INTEGRATION_DATABASE_URL=postgresql://postgres:<pwd>@127.0.0.1:55434/postgres \\
      uv run pytest tests/integration/test_asyncpg_repos.py -v

Tests INSERT rows with platform_id prefix ``__test_asyncpg_`` for
predictable cleanup. The conftest fixture deletes them after each
test even on failure.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import patch

import asyncpg
import pytest

# Marker so pytest -m integration picks these up + unit-only runs skip
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PLATFORM_PREFIX = "__test_asyncpg_"


# ─── Pool fixture ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_pool(integration_db_url):
    """Point the SQLAlchemy engine at the test DSN for the test (the
    asyncpg repos run on app.db.engine now that pg_pool is retired).
    Disposes the engine after so connections don't leak into the next
    test or non-integration tests in the same session."""
    from app.db import engine as db_engine

    # Reset the singleton + override DSN so get_engine() rebuilds against
    # the test database.
    db_engine._engine = None
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None


@pytest.fixture
async def cleanup_test_rows(integration_db_url):
    """After-test cleanup: delete any parsed_media row whose
    platform_id starts with the test prefix. Runs even on failure
    so we don't leak rows."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        # Cascade through resources via the FK on media_id.
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
    """Insert one parsed_media row + return it. Keeps test setup
    explicit (no global fixtures with surprise data)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        platform_id = f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:12]}"
        defaults = {
            "platform_id": platform_id,
            "source_platform": "douyin",
            "title": "Integration test row",
            "original_url": f"https://test.example/{platform_id}",
        }
        defaults.update(overrides)
        cols = list(defaults.keys())
        vals = list(defaults.values())
        placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
        col_list = ", ".join(f'"{c}"' for c in cols)
        row = await conn.fetchrow(
            f"INSERT INTO parsed_media ({col_list}) VALUES ({placeholders}) RETURNING *",
            *vals,
        )
        yield dict(row)
    finally:
        await conn.close()


# ─── Tests for the media repository ────────────────────────────────────
#
# The media repository was migrated off this asyncpg path onto the
# SQLAlchemy ORM (Task 5.1). Its integration tests — including the
# silent-rollback P0 regression — now live in
# tests/integration/test_media_repository_orm.py. This file retains the
# Resources asyncpg tests (that repo has not been migrated yet) plus the
# _bigint array-binding sanity check.


# ─── Tests for ResourcesRepositoryAsyncpg ──────────────────────────────


async def test_resources_get_by_id_with_str_input(
    integration_db_url, patched_pool, cleanup_test_rows
):
    """resources.id is BIGINT (Snowflake). Same str→bigint trap
    as media_repository — pinned by the _bigint coercion fix from
    PR #213's self-review."""
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    # Seed a parsed_media first (resources requires media_id FK).
    async with _seed_parsed_media(integration_db_url) as media:
        # Create a resource. Need a valid creator_id (uuid). Use a
        # well-known test uuid; if it doesn't exist in auth.users
        # the FK might fail — fall back to NULL or use ON CONFLICT.
        conn = await asyncpg.connect(integration_db_url)
        try:
            # Get any real user id from auth.users to satisfy FK
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows to use as test creator_id")

            resource = await conn.fetchrow(
                "INSERT INTO resources (creator_id, media_id, source_type, filename) "
                "VALUES ($1, $2, 'web', 'integration_test.mp4') RETURNING *",
                test_user_id,
                media["id"],
            )
        finally:
            await conn.close()

        repo = ResourcesRepositoryAsyncpg()
        # Pass str (how API path params arrive)
        result = await repo.get_resource_by_id(str(resource["id"]))
        assert result is not None
        assert int(result["id"]) == resource["id"]
        assert result["filename"] == "integration_test.mp4"


async def test_resources_get_by_media_id_with_str_input(
    integration_db_url, patched_pool, cleanup_test_rows
):
    """media_id is BIGINT FK to parsed_media.id. Same trap class."""
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows to use as test creator_id")
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, filename) "
                "VALUES ($1, $2, 'web', 'integration_test.mp4')",
                test_user_id,
                media["id"],
            )
        finally:
            await conn.close()

        repo = ResourcesRepositoryAsyncpg()
        result = await repo.get_resource_by_media_id(str(media["id"]))
        assert result is not None
        assert int(result["media_id"]) == media["id"]


async def test_count_resources_by_media_id_returns_int(
    integration_db_url, patched_pool, cleanup_test_rows
):
    """Verifies COUNT(*) returns an int (not str), and that the
    str→bigint coercion on media_id binding works."""
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows to use as test creator_id")
            for i in range(3):
                await conn.execute(
                    "INSERT INTO resources (creator_id, media_id, source_type, filename) "
                    "VALUES ($1, $2, 'web', $3)",
                    test_user_id,
                    media["id"],
                    f"test_{i}.mp4",
                )
        finally:
            await conn.close()

        repo = ResourcesRepositoryAsyncpg()
        count = await repo.count_resources_by_media_id(str(media["id"]))
        assert isinstance(count, int)
        assert count == 3


async def test_get_completed_resource_l2_dedup_shape(
    integration_db_url, patched_pool, cleanup_test_rows
):
    """L2 dedup probe is the most-called supabase-py path under
    Bug C. Verify the JOIN works and the nested
    {id, media_id, parsed_media: {...}} shape is preserved."""
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    test_url = f"https://test.example/{uuid.uuid4().hex}"
    async with _seed_parsed_media(
        integration_db_url,
        original_url=test_url,
        video_download_status="completed",
        media_type="0",  # video, not image
    ) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            test_user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
            if not test_user_id:
                pytest.skip("No auth.users rows to use as test creator_id")
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, filename) "
                "VALUES ($1, $2, 'web', 'l2_test.mp4')",
                test_user_id,
                media["id"],
            )
        finally:
            await conn.close()

        repo = ResourcesRepositoryAsyncpg()
        result = await repo.get_completed_resource_by_url_and_creator(
            test_url, str(test_user_id)
        )
        assert result is not None, "L2 dedup probe failed to find seeded row"
        # Verify nested shape
        assert "id" in result
        assert "media_id" in result
        assert "parsed_media" in result
        pm = result["parsed_media"]
        assert isinstance(
            pm, dict
        ), f"parsed_media should be dict, got {type(pm).__name__}"
        assert pm["original_url"] == test_url
        assert pm["video_download_status"] == "completed"


# ─── _bigint helper sanity (unit) ──────────────────────────────────────


async def test_bigint_array_binding_works():
    """ANY($1::bigint[]) was a Phase 3b bug — tested str list against
    bigint column failed silently. Pin the fix end-to-end."""
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set")
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        # int list works
        rows = await conn.fetch(
            "SELECT id FROM parsed_media WHERE id = ANY($1::bigint[]) LIMIT 1",
            [1, 2, 3],
        )
        # Should not raise (may return 0 rows if those IDs don't exist)
        assert isinstance(rows, list)

        # str list against bigint column DOES NOT WORK — this is the trap
        with pytest.raises(asyncpg.exceptions.DataError):
            await conn.fetch(
                "SELECT id FROM parsed_media WHERE id = ANY($1::bigint[]) LIMIT 1",
                ["1", "2", "3"],
            )
    finally:
        await conn.close()
