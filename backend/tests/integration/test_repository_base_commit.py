"""Commit-boundary integration tests for ``AsyncpgRepository.insert`` /
``update_by_id`` (Task 5.5).

THE BUG CLASS (the whole reason for the ORM migration — #498 / silent-rollback
P0): ``db_engine.fetch_one`` / ``fetch_val`` run on ``engine.connect()`` (NO
transaction → NEVER commits). An ``INSERT/UPDATE ... RETURNING`` issued through
``fetch_one`` EXECUTES, hands back the RETURNING row (visible within the
connection), then SILENTLY ROLLS BACK on connection close — the caller thinks it
persisted but it didn't.

The base ``insert`` / ``update_by_id`` used to do exactly that. These tests prove
the fix: a base ``insert(...)`` / ``update_by_id(...)`` must be visible to a
SEPARATE fresh connection (proving the write COMMITTED).

RED→GREEN evidence: temporarily revert ``insert`` to ``self.fetch_one`` → the
read-back assertion FAILS (the row never lands). Restore
``execute_returning_one`` → GREEN.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub schema.
Skips otherwise. Uses a throwaway temp-named table created/dropped per module so
it touches no real data and needs no FK target.

    source /tmp/orm2_integration.env  # sets INTEGRATION_DATABASE_URL
    uv run pytest tests/integration/test_repository_base_commit.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# A throwaway table name — unique per run so parallel runs never collide.
_TABLE = f"orm2_commit_probe_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def probe_table(integration_db_url):
    """Create/drop a throwaway table for the duration of one test. Plain
    columns, no FKs, so no real data is touched."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(f'DROP TABLE IF EXISTS public."{_TABLE}"')
        await conn.execute(
            f'CREATE TABLE public."{_TABLE}" (id bigint PRIMARY KEY, label text)'
        )
    finally:
        await conn.close()
    yield _TABLE
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(f'DROP TABLE IF EXISTS public."{_TABLE}"')
    finally:
        await conn.close()


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine at the test DSN so the base methods (which
    route through ``db_engine``) hit the test DB. Reset the singleton."""
    from unittest.mock import patch

    from app.db import engine as db_engine

    db_engine._engine = None
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None


def _repo(table: str):
    from app.db.repository_base import AsyncpgRepository

    class _ProbeRepo(AsyncpgRepository):
        TABLE = table

    return _ProbeRepo()


async def _read_label_fresh(dsn: str, row_id: int) -> str | None:
    """Read the row's label from a SEPARATE asyncpg connection — the commit
    proof. If the write rolled back, this returns None."""
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            f'SELECT label FROM public."{_TABLE}" WHERE id = $1', row_id
        )
    finally:
        await conn.close()


async def test_insert_commits_visible_to_fresh_connection(
    probe_table, patched_engine, integration_db_url
):
    """A base ``insert(...)`` must COMMIT — visible to a separate connection.

    This is the RED→GREEN pivot: with ``self.fetch_one`` (non-committing
    connect()) the fresh-connection read returns None (silent rollback); with
    ``execute_returning_one`` (committing begin()) it returns the value."""
    repo = _repo(probe_table)

    returned = await repo.insert(id=101, label="committed-row")
    # The RETURNING row is handed back (true on both the broken and fixed path).
    assert returned is not None
    assert returned["id"] == 101
    assert returned["label"] == "committed-row"

    # THE COMMIT PROOF: a fresh connection must see the row.
    seen = await _read_label_fresh(integration_db_url, 101)
    assert seen == "committed-row", (
        "INSERT silently rolled back — base insert() ran on a non-committing "
        "connection (the #498 silent-rollback class)"
    )


async def test_update_by_id_commits_visible_to_fresh_connection(
    probe_table, patched_engine, integration_db_url
):
    """A base ``update_by_id(...)`` must COMMIT — visible to a separate
    connection, and return the post-update row."""
    repo = _repo(probe_table)

    # Seed via the (now-committing) insert.
    await repo.insert(id=202, label="before")
    assert await _read_label_fresh(integration_db_url, 202) == "before"

    updated = await repo.update_by_id(202, label="after")
    assert updated is not None
    assert updated["label"] == "after"

    # THE COMMIT PROOF: the new value must be visible to a fresh connection.
    seen = await _read_label_fresh(integration_db_url, 202)
    assert seen == "after", (
        "UPDATE silently rolled back — base update_by_id() ran on a "
        "non-committing connection (the #498 silent-rollback class)"
    )


async def test_update_by_id_returns_none_when_no_row_matched(
    probe_table, patched_engine, integration_db_url
):
    """No matching row → None (return shape preserved on the committing path)."""
    repo = _repo(probe_table)
    result = await repo.update_by_id(999999, label="ghost")
    assert result is None
