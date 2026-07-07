"""Integration tests for SocialAccountsRepository.upsert_account vs real PG.

THE BUG CLASS (#498 silent-rollback): ``upsert_account`` used to run its
``INSERT ... ON CONFLICT ... RETURNING *`` through ``self.fetch_one``, which
executes on ``eng.connect()`` (no transaction). The write EXECUTES and hands
back a RETURNING row within that connection, then SILENTLY ROLLS BACK on
connection close — the caller (OAuth bind flow) believed the account was
saved but it never persisted. Fixed to route through
``db_engine.execute_returning_one`` (``eng.begin()``, auto-commit).

These tests prove the fix: a row written by ``upsert_account`` must be
visible to a SEPARATE fresh connection (proving the write COMMITTED), and a
second upsert on the same natural key must UPDATE in place (ON CONFLICT).

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub
schema (migration 350 applied). Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_social_accounts_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PLATFORM_PREFIX = "__test_orm_social_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from unittest.mock import patch

    from app.db import engine as db_engine

    db_engine._engine = None
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None


@pytest.fixture
async def a_user(integration_db_url):
    """Yield one REAL auth.users id — created_by has no FK today but we stay
    defensive in case one is added later (same convention as the cookies
    integration test)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if not row:
            pytest.skip("need >=1 auth.users row")
        yield row["id"]
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_social_accounts(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM social_accounts WHERE platform LIKE $1", _PLATFORM_PREFIX + "%"
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.social_accounts_repository import SocialAccountsRepository

    return SocialAccountsRepository()


def _platform() -> str:
    return f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:8]}"


async def _read_fresh(dsn: str, scope_id: str, platform: str) -> dict | None:
    """Read the row via a SEPARATE asyncpg connection — the commit proof. If
    the write rolled back (the #498 class), this returns None."""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM social_accounts "
            "WHERE scope_type = 'user' AND scope_id = $1 AND platform = $2",
            scope_id,
            platform,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def test_upsert_account_commits_visible_to_fresh_connection(
    integration_db_url, patched_engine, cleanup_social_accounts, a_user
):
    """A ``upsert_account(...)`` INSERT must COMMIT — visible to a separate
    connection. RED→GREEN: with ``self.fetch_one`` (non-committing connect())
    the fresh-connection read returns None (silent rollback); with
    ``db_engine.execute_returning_one`` (committing begin()) it returns the
    row."""
    scope_id = str(uuid.uuid4())
    platform = _platform()

    saved = await _repo().upsert_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id="open_id_1",
        username="creator_one",
        avatar_url="https://example.com/a.png",
        access_token="act-plain",
        refresh_token="rft-plain",
        token_expires_at=None,
        created_by=str(a_user),
    )
    assert saved is not None
    assert saved["username"] == "creator_one"
    assert "access_token" not in saved  # _public_row strips token cols
    assert "refresh_token" not in saved

    # THE COMMIT PROOF: a fresh connection must see the row.
    seen = await _read_fresh(integration_db_url, scope_id, platform)
    assert seen is not None, (
        "INSERT silently rolled back — upsert_account ran on a "
        "non-committing connection (the #498 silent-rollback class)"
    )
    assert seen["username"] == "creator_one"
    # Tokens are Fernet-encrypted at rest, never plaintext.
    assert seen["access_token"] != "act-plain"
    assert seen["access_token"].startswith("gAAAAA")


async def test_upsert_account_on_conflict_updates_in_place(
    integration_db_url, patched_engine, cleanup_social_accounts, a_user
):
    scope_id = str(uuid.uuid4())
    platform = _platform()
    created_by = str(a_user)

    first = await _repo().upsert_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id="open_id_2",
        username="v1",
        avatar_url=None,
        access_token=None,
        refresh_token=None,
        token_expires_at=None,
        created_by=created_by,
    )
    assert first["username"] == "v1"

    # Second upsert on the SAME natural key (scope_type, scope_id, platform,
    # platform_user_id) → UPDATE, not a 2nd row.
    second = await _repo().upsert_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id="open_id_2",
        username="v2-changed",
        avatar_url=None,
        access_token=None,
        refresh_token=None,
        token_expires_at=None,
        created_by=created_by,
    )
    assert second["username"] == "v2-changed"
    assert second["id"] == first["id"]  # same row (conflict → update)

    conn = await asyncpg.connect(integration_db_url)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM social_accounts "
            "WHERE scope_type = 'user' AND scope_id = $1 AND platform = $2",
            scope_id,
            platform,
        )
    finally:
        await conn.close()
    assert count == 1  # committed, single row (ON CONFLICT DO UPDATE)
