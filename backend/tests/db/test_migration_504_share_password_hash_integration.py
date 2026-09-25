"""DB-backed guard for migration 504: share passwords become bcrypt hashes.

Only Postgres can answer these: the backfill and the trigger hash with
pgcrypto's ``crypt()``, the backend checks with the Python ``bcrypt`` binding,
and the two have to agree on the same bytes (including bcrypt's 72-byte cut,
which pgcrypto applies silently and the binding refuses). The unit lane never
runs pgcrypto.

Pinned, all inside rolled-back transactions on production-shaped rows:

  (a) the backfill hashes a plain-text password, overwrites the column with a
      ``!locked:`` value, and leaves password-less rows alone;
  (b) the migration is idempotent: a second run changes no hash;
  (c) the trigger hashes a legacy writer's plain text on INSERT and UPDATE,
      and leaves rows written by the new code (hash + lock) exactly as given;
  (d) pgcrypto and the Python binding verify each other's hashes, including a
      multi-byte password longer than 72 bytes;
  (e) browser roles cannot execute the trigger function.

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_migration_504_share_password_hash_integration.py
"""

from __future__ import annotations

import os
import pathlib
import uuid

import pytest

pytest.importorskip("asyncpg")

import asyncpg  # noqa: E402

from app.services.library.share_passwords import (  # noqa: E402
    LEGACY_PASSWORD_LOCK_PREFIX,
    hash_share_password,
    password_matches,
    share_password_columns,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 504 tests need a DB.",
)

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "504_shares_password_hash.sql"
)

# 100 CJK characters = 300 UTF-8 bytes: well past bcrypt's 72.
_LONG_PASSWORD = "密" * 100


def _migration_body() -> str:
    """The migration without its own BEGIN/COMMIT, so it runs inside the
    test's transaction and rolls back with it."""
    lines = _MIGRATION.read_text().splitlines()
    return "\n".join(
        line for line in lines if line.strip() not in ("BEGIN;", "COMMIT;")
    )


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


async def _new_user(pg) -> str:
    uid = str(uuid.uuid4())
    await pg.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2)",
        uid,
        f"share-pw-{uid[:8]}@example.com",
    )
    return uid


async def _insert_share(pg, user_id: str, **columns) -> int:
    code = uuid.uuid4().hex[:12]
    base = {
        "share_type": "link",
        "shared_by": user_id,
        "share_name": "Test Share",
        "share_code": code,
    }
    base.update(columns)
    names = ", ".join(base)
    params = ", ".join(
        f"${i}::uuid" if name == "shared_by" else f"${i}"
        for i, name in enumerate(base, start=1)
    )
    return await pg.fetchval(
        f"INSERT INTO public.shares ({names}) VALUES ({params}) RETURNING id",
        *base.values(),
    )


async def _row(pg, share_id: int) -> asyncpg.Record:
    return await pg.fetchrow(
        "SELECT password, password_hash FROM public.shares WHERE id = $1", share_id
    )


async def _legacy_rows(pg, user_id: str) -> dict[str, int]:
    """Rows as they were before mig 504: plain text, no hash. The trigger
    is suppressed for this transaction only, so the rows land as written."""
    await pg.execute("SET LOCAL session_replication_role = replica")
    rows = {
        "plain": await _insert_share(pg, user_id, password="hunter2"),
        "long": await _insert_share(pg, user_id, password=_LONG_PASSWORD),
        "none": await _insert_share(pg, user_id),
    }
    await pg.execute("SET LOCAL session_replication_role = origin")
    return rows


@_skip
async def test_backfill_hashes_plain_text_and_locks_the_column(pg):
    user = await _new_user(pg)
    rows = await _legacy_rows(pg, user)

    await pg.execute(_migration_body())

    plain = await _row(pg, rows["plain"])
    assert plain["password"].startswith(LEGACY_PASSWORD_LOCK_PREFIX)
    assert "hunter2" not in plain["password"]
    assert plain["password_hash"].startswith("$2")
    assert password_matches(plain["password_hash"], "hunter2")
    assert not password_matches(plain["password_hash"], "hunter3")

    none = await _row(pg, rows["none"])
    assert (none["password"], none["password_hash"]) == (None, None)


@_skip
async def test_a_second_run_changes_no_hash(pg):
    user = await _new_user(pg)
    rows = await _legacy_rows(pg, user)
    await pg.execute(_migration_body())
    first = await _row(pg, rows["plain"])

    await pg.execute(_migration_body())

    assert await _row(pg, rows["plain"]) == first


@_skip
async def test_pgcrypto_and_python_agree_past_72_bytes(pg):
    user = await _new_user(pg)
    rows = await _legacy_rows(pg, user)
    await pg.execute(_migration_body())

    # pgcrypto hashed it (backfill); Python verifies it.
    long_row = await _row(pg, rows["long"])
    assert password_matches(long_row["password_hash"], _LONG_PASSWORD)

    # Python hashes it; pgcrypto verifies it.
    for password in ("hunter2", _LONG_PASSWORD):
        hashed = hash_share_password(password)
        crypto = await pg.fetchval(
            "SELECT n.nspname FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace "
            "WHERE e.extname = 'pgcrypto'"
        )
        same = await pg.fetchval(
            f'SELECT "{crypto}".crypt($1, $2) = $2', password, hashed
        )
        assert same, password[:8]


@_skip
async def test_trigger_hashes_a_legacy_insert(pg):
    await pg.execute(_migration_body())  # the file under test, not whatever ran last
    user = await _new_user(pg)
    share = await _insert_share(pg, user, password="legacy-writer")

    row = await _row(pg, share)
    assert row["password"].startswith(LEGACY_PASSWORD_LOCK_PREFIX)
    assert password_matches(row["password_hash"], "legacy-writer")


@_skip
async def test_trigger_hashes_a_legacy_update(pg):
    await pg.execute(_migration_body())  # the file under test, not whatever ran last
    user = await _new_user(pg)
    share = await _insert_share(pg, user, password="first")
    before = await _row(pg, share)

    await pg.execute(
        "UPDATE public.shares SET password = 'second' WHERE id = $1", share
    )

    row = await _row(pg, share)
    assert row["password_hash"] != before["password_hash"]
    assert password_matches(row["password_hash"], "second")
    assert not password_matches(row["password_hash"], "first")
    assert row["password"].startswith(LEGACY_PASSWORD_LOCK_PREFIX)


@_skip
async def test_trigger_leaves_new_code_rows_and_other_updates_alone(pg):
    await pg.execute(_migration_body())  # the file under test, not whatever ran last
    user = await _new_user(pg)
    columns = share_password_columns("new-code")
    share = await _insert_share(pg, user, **columns)

    row = await _row(pg, share)
    assert dict(row) == columns

    await pg.execute(
        "UPDATE public.shares SET view_count = view_count + 1 WHERE id = $1", share
    )
    assert dict(await _row(pg, share)) == columns


@_skip
async def test_browser_roles_cannot_execute_the_trigger_function(pg):
    # CREATE OR REPLACE keeps an existing function's ACL, so re-running the
    # file over an already-migrated database would pass even without the
    # REVOKE. Drop it first: this is what a fresh install sees.
    await pg.execute(
        "DROP TRIGGER IF EXISTS shares_hash_plaintext_password ON public.shares;"
        "DROP FUNCTION IF EXISTS public.shares_hash_plaintext_password();"
    )
    await pg.execute(_migration_body())
    for role in ("anon", "authenticated"):
        allowed = await pg.fetchval(
            "SELECT has_function_privilege($1, "
            "'public.shares_hash_plaintext_password()', 'EXECUTE')",
            role,
        )
        assert allowed is False, role
    # Negative control: the owner still can (a REVOKE that hit everyone
    # would pass the loop above for the wrong reason).
    assert await pg.fetchval(
        "SELECT has_function_privilege('postgres', "
        "'public.shares_hash_plaintext_password()', 'EXECUTE')"
    )
