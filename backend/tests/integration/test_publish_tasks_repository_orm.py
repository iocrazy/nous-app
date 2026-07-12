"""Integration tests for PublishTasksRepository's committing write paths.

THE BUG CLASS (#498 silent-rollback): an ``INSERT ... RETURNING`` run through
``self.fetch_one`` executes on ``eng.connect()`` (no transaction) — the write
EXECUTES and hands back a RETURNING row within that connection, then SILENTLY
ROLLS BACK on connection close. The D1 review caught this pattern twice
(``social_accounts_repository.upsert_account``); this repo's ``create_task``
and ``create_task_account`` route through ``db_engine.execute_returning_one``
(``eng.begin()``, auto-commit) instead.

These tests prove the fix: rows written by ``create_task`` /
``create_task_account`` must be visible to a SEPARATE fresh connection
(proving the write COMMITTED).

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub
schema (migration 356 applied). Skips cleanly otherwise:

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
        uv run pytest tests/integration/test_publish_tasks_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PLATFORM_PREFIX = "__test_publish_tasks_"


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
    """Yield one REAL auth.users id — publish_tasks.user_id has no FK today
    but we stay defensive, same convention as the D1 social_accounts test."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if not row:
            pytest.skip("need >=1 auth.users row")
        yield row["id"]
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_publish_rows(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM publish_tasks WHERE title LIKE $1", _PLATFORM_PREFIX + "%"
        )
        await conn.execute(
            "DELETE FROM social_accounts WHERE platform LIKE $1", _PLATFORM_PREFIX + "%"
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.publish_tasks_repository import PublishTasksRepository

    return PublishTasksRepository()


def _social_repo():
    from app.repositories.social_accounts_repository import SocialAccountsRepository

    return SocialAccountsRepository()


def _title() -> str:
    return f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:8]}"


async def _read_fresh_task(dsn: str, title: str) -> dict | None:
    """Read the row via a SEPARATE asyncpg connection — the commit proof. If
    the write rolled back (the #498 class), this returns None."""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM publish_tasks WHERE title = $1", title)
        return dict(row) if row else None
    finally:
        await conn.close()


async def _read_fresh_task_account(dsn: str, task_id: int) -> dict | None:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM publish_task_accounts WHERE task_id = $1", task_id
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def test_create_task_commits_visible_to_fresh_connection(
    integration_db_url, patched_engine, cleanup_publish_rows, a_user
):
    """A ``create_task(...)`` INSERT must COMMIT — visible to a separate
    connection. RED→GREEN: with ``self.fetch_one`` (non-committing connect())
    the fresh-connection read returns None (silent rollback); with
    ``db_engine.execute_returning_one`` (committing begin()) it returns the
    row."""
    title = _title()

    saved = await _repo().create_task(
        user_id=str(a_user),
        title=title,
        resource_ids=["1", "2"],
        topics=["travel"],
    )
    assert saved is not None
    assert saved["title"] == title
    assert isinstance(saved["id"], str)  # BIGINT → str

    # THE COMMIT PROOF: a fresh connection must see the row.
    seen = await _read_fresh_task(integration_db_url, title)
    assert seen is not None, (
        "INSERT silently rolled back — create_task ran on a "
        "non-committing connection (the #498 silent-rollback class)"
    )
    assert seen["title"] == title
    assert str(seen["id"]) == saved["id"]


async def test_create_task_account_commits_visible_to_fresh_connection(
    integration_db_url, patched_engine, cleanup_publish_rows, a_user
):
    """Same commit proof for ``create_task_account``, which FK-references a
    real ``publish_tasks`` row and a real ``social_accounts`` row."""
    title = _title()
    platform = _title()

    task = await _repo().create_task(user_id=str(a_user), title=title)
    account = await _social_repo().upsert_account(
        scope_type="user",
        scope_id=str(uuid.uuid4()),
        platform=platform,
        platform_user_id="open_id_1",
        username="creator_one",
        avatar_url=None,
        access_token=None,
        refresh_token=None,
        token_expires_at=None,
        created_by=str(a_user),
    )

    saved = await _repo().create_task_account(
        task_id=task["id"], account_id=account["id"], channel="h5"
    )
    assert saved is not None
    assert saved["task_id"] == task["id"]
    assert saved["status"] == "pending"

    # THE COMMIT PROOF: a fresh connection must see the row.
    seen = await _read_fresh_task_account(integration_db_url, int(task["id"]))
    assert seen is not None, (
        "INSERT silently rolled back — create_task_account ran on a "
        "non-committing connection (the #498 silent-rollback class)"
    )
    assert str(seen["task_id"]) == task["id"]
    assert str(seen["account_id"]) == account["id"]
