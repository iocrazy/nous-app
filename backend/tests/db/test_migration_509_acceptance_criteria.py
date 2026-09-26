"""509 on a real Postgres: the two CHECKs bite, issue_create_atomic carries
the new columns, and the columns are NOT on mig 170's immutable list — an
UPDATE by a role the trigger enforces goes through.

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55499/drift \
      uv run pytest tests/db/test_migration_509_acceptance_criteria.py -v

Each test runs the migration and its probes in ONE asyncpg transaction that
is rolled back. Plain asyncpg, not SQLAlchemy: the migration is multi-statement,
which only asyncpg's simple-query ``execute`` accepts, and a raw execute under
SQLAlchemy's lazily-begun transaction runs in autocommit — it would commit the
DDL to the shared database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
if not _TEST_DSN:
    pytest.skip("INTEGRATION_DATABASE_URL not set", allow_module_level=True)

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "509_issue_acceptance_criteria_and_verification_event.sql"
)


def _migration_sql() -> str:
    # The fixture owns the transaction; the file's own BEGIN / COMMIT would
    # end it. The plpgsql body's bare ``BEGIN`` has no semicolon.
    return (
        _MIGRATION.read_text(encoding="utf-8")
        .replace("BEGIN;", "")
        .replace("COMMIT;", "")
    )


@pytest.fixture
async def conn():
    import asyncpg

    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    try:
        assert c.is_in_transaction(), "migration must not run in autocommit"
        await c.execute(_migration_sql())
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _creator(c) -> str:
    """``issues_creator_required``: some auth.users row to hang the probe on."""
    user_id = await c.fetchval("SELECT id::text FROM auth.users LIMIT 1")
    if user_id is None:
        pytest.skip("drift DB has no auth.users row for issues_creator_required")
    return user_id


async def _insert_issue(c) -> int:
    return int(
        await c.fetchval(
            "INSERT INTO public.issues (issue_number, identifier, title, "
            "origin_fingerprint, created_by_user_id) "
            "VALUES (990001, 'T-990001', 'probe', 'probe-509', $1::uuid) "
            "RETURNING id",
            await _creator(c),
        )
    )


async def test_length_check_bites(conn):
    iid = await _insert_issue(conn)
    with pytest.raises(Exception, match="issues_acceptance_criteria_len_check"):
        await conn.execute(
            "UPDATE public.issues SET acceptance_criteria = repeat('x', 4001) "
            "WHERE id = $1",
            iid,
        )


async def test_source_check_rejects_unknown(conn):
    iid = await _insert_issue(conn)
    with pytest.raises(Exception, match="issues_acceptance_criteria_source_check"):
        await conn.execute(
            "UPDATE public.issues SET acceptance_criteria_source = 'bot' "
            "WHERE id = $1",
            iid,
        )


async def test_create_atomic_persists_the_new_columns(conn):
    """173's INSERT is an explicit column list; 509 must extend it or POST
    /issues silently drops the criteria."""
    payload = json.dumps(
        {
            "title": "probe",
            "origin_fingerprint": "probe-509-create",
            "acceptance_criteria": "two shots",
            "acceptance_criteria_source": "user",
            "created_by_user_id": await _creator(conn),
        }
    )
    row = await conn.fetchrow(
        "SELECT acceptance_criteria, acceptance_criteria_source "
        "FROM public.issue_create_atomic($1::jsonb)",
        payload,
    )
    assert tuple(row) == ("two shots", "user")


async def test_create_atomic_stays_closed_to_browser_roles(conn):
    row = await conn.fetchrow(
        "SELECT has_function_privilege('anon', "
        "'public.issue_create_atomic(jsonb)', 'EXECUTE'), "
        "has_function_privilege('authenticated', "
        "'public.issue_create_atomic(jsonb)', 'EXECUTE')"
    )
    assert tuple(row) == (False, False)


async def test_mig170_trigger_lets_the_new_columns_through(conn):
    """Deviation 1: mig 170 lists the IMMUTABLE columns; these are not on it.

    The trigger bypasses only ``service_role`` / ``supabase_admin`` — the
    ``postgres`` role this test connects as is enforced like ``authenticated``
    (which the drift DB grants nothing on ``issues``, so it cannot be used)."""
    iid = await _insert_issue(conn)
    await conn.execute(
        "UPDATE public.issues SET acceptance_criteria = 'two shots', "
        "acceptance_criteria_source = 'user' WHERE id = $1",
        iid,
    )
    row = await conn.fetchrow(
        "SELECT acceptance_criteria, acceptance_criteria_source "
        "FROM public.issues WHERE id = $1",
        iid,
    )
    assert tuple(row) == ("two shots", "user")


async def test_mig170_trigger_is_live_for_this_role(conn):
    """Positive control: without it the test above passes vacuously on a DB
    whose trigger is missing or bypassed for ``postgres``."""
    iid = await _insert_issue(conn)
    with pytest.raises(Exception, match="not in user-allowlist"):
        await conn.execute(
            "UPDATE public.issues SET origin_id = 'moved' WHERE id = $1", iid
        )
