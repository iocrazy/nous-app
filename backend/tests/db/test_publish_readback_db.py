"""Guard for migration 418 + the read-back sweep's SQL, on a real Postgres.

WHY THIS FILE EXISTS
────────────────────
The rest of the P1-3 tests run against fakes, which can prove the decisions but
not the two things that have actually broken on this codebase before:

  * that the migration's columns exist with the types/defaults the ORM model
    declares (the `parsed_media.transcript_status` class of drift — code that
    reads a column nobody ever added, failing silently for months);
  * that the sweep's SELECT — a correlated `array_agg` subquery and a
    `NULLS FIRST` ordering — actually executes on Postgres. A statement that
    compiles under SQLAlchemy can still be rejected by the server.

Everything here therefore executes the repository's OWN statement builders
(`readback_due_stmt` / `verification_update_stmt`) against the CI-built
ephemeral schema, the same one `test_schema_drift.py` uses. They go through raw
asyncpg rather than the app's session on purpose: `read_scope()` resolves the
DSN from app settings, which would quietly point these writes at the
developer's real database instead of the throwaway one. Skips cleanly when
INTEGRATION_DATABASE_URL is unset.

Reverse-verified: dropping the four columns from migration 418 turns
`test_migration_418_added_the_verify_columns` red, and reverting
`readback_due_stmt`'s `channel='session'` filter turns
`test_only_session_channel_rows_are_due` red.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from sqlalchemy.dialects import postgresql

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytestmark.append(
    pytest.mark.skipif(
        not _TEST_DSN,
        reason="INTEGRATION_DATABASE_URL unset — needs a CI-built Postgres",
    )
)


def _sql(stmt) -> str:
    """Compile a SQLAlchemy statement to executable SQL.

    `literal_binds` because these run through raw asyncpg rather than the app's
    session: the point is to execute the REAL statement the repository builds,
    on a real server, without dragging in the app's DSN resolution (which would
    silently target the developer's own database instead of the ephemeral one).
    """
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


EXPECTED_COLUMNS = {
    "verify_state": ("text", "YES"),
    "verify_attempts": ("integer", "NO"),
    "verify_detail": ("text", "YES"),
    "verify_checked_at": ("timestamp with time zone", "YES"),
}


@pytest.fixture
async def conn():
    connection = await asyncpg.connect(_TEST_DSN)
    try:
        yield connection
    finally:
        await connection.close()


async def test_migration_418_added_the_verify_columns(conn):
    rows = await conn.fetch(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'publish_task_accounts'
          AND column_name LIKE 'verify%'
        """
    )
    found = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in rows}
    assert found == EXPECTED_COLUMNS

    default = next(
        r["column_default"] for r in rows if r["column_name"] == "verify_attempts"
    )
    # NOT NULL DEFAULT 0 is what makes `verify_attempts + 1` safe in
    # `record_verification` — on a nullable column that expression yields NULL
    # and the retry budget would never be reached, so a row that can never be
    # confirmed would also never be abandoned.
    assert default is not None and "0" in default


async def test_the_due_index_exists_and_is_partial(conn):
    definition = await conn.fetchval(
        """
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'publish_task_accounts'
          AND indexname = 'idx_publish_task_accounts_readback_due'
        """
    )
    assert definition is not None, "migration 418's partial index is missing"
    # Partial, not full: the due set is a tiny minority of the table and stays
    # tiny. A full index here would be paid for on every publish write.
    assert "WHERE" in definition
    assert "'success'" in definition and "'session'" in definition


async def _seed(conn, **overrides):
    """One publish_tasks + publish_task_accounts + social_accounts triple."""
    user_id = uuid.uuid4()
    account_id = await conn.fetchval("SELECT generate_snowflake_id()")
    task_id = await conn.fetchval("SELECT generate_snowflake_id()")
    row_id = await conn.fetchval("SELECT generate_snowflake_id()")

    await conn.execute(
        """
        INSERT INTO public.social_accounts
            (id, scope_type, scope_id, platform, platform_user_id, username,
             created_by, auth_type)
        VALUES ($1, 'user', $2, 'douyin', $3, $4, $5, 'session')
        """,
        account_id,
        str(user_id),
        f"uid-{row_id}",
        "Test Creator",
        user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.publish_tasks (id, user_id, content_type, title, scheduled_at)
        VALUES ($1, $2, 'video', $3, $4)
        """,
        task_id,
        user_id,
        overrides.get("task_title", "Batch Title"),
        overrides.get("scheduled_at"),
    )
    await conn.execute(
        """
        INSERT INTO public.publish_task_accounts
            (id, task_id, account_id, channel, status, title, verify_state,
             verify_attempts, verify_checked_at, published_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        row_id,
        task_id,
        account_id,
        overrides.get("channel", "session"),
        overrides.get("status", "success"),
        overrides.get("row_title"),
        overrides.get("verify_state"),
        overrides.get("verify_attempts", 0),
        overrides.get("verify_checked_at"),
        overrides.get("published_at", datetime.now(timezone.utc) - timedelta(hours=2)),
    )
    return {"row_id": row_id, "task_id": task_id, "account_id": account_id}


async def test_list_readback_due_runs_on_postgres_and_picks_the_right_rows(conn):
    """The SELECT is a real query, not just something that compiles.

    Also pins the title precedence: the per-account override wins over the
    batch default, exactly as `_account_publish_opts` resolves it at publish
    time. Reading a different title here than the one that was typed into the
    editor would make every read-back a false `not_found`.
    """
    from app.repositories.publish_tasks_repository import readback_due_stmt

    seeded = await _seed(conn, task_title="Batch Title", row_title="Account Override")
    try:
        rows = await conn.fetch(_sql(readback_due_stmt(50)))
    finally:
        await conn.execute(
            "DELETE FROM public.publish_tasks WHERE id = $1", seeded["task_id"]
        )

    mine = [r for r in rows if str(r["id"]) == str(seeded["row_id"])]
    assert len(mine) == 1
    assert mine[0]["title"] == "Account Override"
    assert mine[0]["platform"] == "douyin"


@pytest.mark.parametrize(
    "overrides,should_appear",
    [
        ({}, True),
        ({"verify_state": "pending"}, True),
        # Terminal verdicts leave the due set for good.
        ({"verify_state": "verified"}, False),
        ({"verify_state": "not_live"}, False),
        ({"verify_state": "abandoned"}, False),
        # Only the session channel is ever read back: official/h5 rows get
        # their URL from the API that created them.
        ({"channel": "h5"}, False),
        ({"channel": "official"}, False),
        # Nothing to confirm about a publish that failed.
        ({"status": "failed"}, False),
        ({"status": "pending_share"}, False),
    ],
)
async def test_only_session_channel_rows_are_due(conn, overrides, should_appear):
    from app.repositories.publish_tasks_repository import readback_due_stmt

    seeded = await _seed(conn, **overrides)
    try:
        rows = await conn.fetch(_sql(readback_due_stmt(50)))
    finally:
        await conn.execute(
            "DELETE FROM public.publish_tasks WHERE id = $1", seeded["task_id"]
        )

    appeared = any(str(r["id"]) == str(seeded["row_id"]) for r in rows)
    assert appeared is should_appear


async def test_record_verification_fills_the_url_and_bumps_attempts(conn):
    """The P1-3 headline: after a verified read-back the row finally HAS a
    published_url — the column that has been empty on every record so far."""
    from app.repositories.publish_tasks_repository import verification_update_stmt

    seeded = await _seed(conn)
    try:
        await conn.execute(
            _sql(
                verification_update_stmt(
                    seeded["row_id"],
                    state="verified",
                    published_url="https://www.douyin.com/video/7412345678901234567",
                    platform_item_id="7412345678901234567",
                )
            )
        )
        row = await conn.fetchrow(
            """
            SELECT verify_state, verify_attempts, verify_checked_at,
                   published_url, platform_item_id, status
            FROM public.publish_task_accounts WHERE id = $1
            """,
            seeded["row_id"],
        )
        assert row["verify_state"] == "verified"
        assert row["verify_attempts"] == 1
        assert row["verify_checked_at"] is not None
        assert row["published_url"].endswith("7412345678901234567")
        assert row["platform_item_id"] == "7412345678901234567"
        # Route C / history: the upload really did succeed. The read-back's
        # verdict is a separate fact and must not rewrite it.
        assert row["status"] == "success"
    finally:
        await conn.execute(
            "DELETE FROM public.publish_tasks WHERE id = $1", seeded["task_id"]
        )


async def test_an_inconclusive_attempt_never_blanks_an_established_url(conn):
    """A later failure must not erase what an earlier success established —
    otherwise one container outage costs the user a link they already had."""
    from app.repositories.publish_tasks_repository import verification_update_stmt

    seeded = await _seed(conn)
    try:
        await conn.execute(
            _sql(
                verification_update_stmt(
                    seeded["row_id"],
                    state="verified",
                    published_url="https://www.douyin.com/video/7412345678901234567",
                    platform_item_id="7412345678901234567",
                )
            )
        )
        await conn.execute(
            _sql(
                verification_update_stmt(
                    seeded["row_id"],
                    state="pending",
                    detail="[unreachable] container down",
                )
            )
        )
        row = await conn.fetchrow(
            "SELECT published_url, verify_attempts, verify_state "
            "FROM public.publish_task_accounts WHERE id = $1",
            seeded["row_id"],
        )
        assert row["published_url"].endswith("7412345678901234567")
        assert row["verify_attempts"] == 2
    finally:
        await conn.execute(
            "DELETE FROM public.publish_tasks WHERE id = $1", seeded["task_id"]
        )


async def test_abandon_does_not_burn_another_attempt(conn):
    """`bump_attempts=False` — abandoning is closing the book on a run of
    failures, not one more failure."""
    from app.repositories.publish_tasks_repository import verification_update_stmt

    seeded = await _seed(conn, verify_state="pending", verify_attempts=5)
    try:
        await conn.execute(
            _sql(
                verification_update_stmt(
                    seeded["row_id"],
                    state="abandoned",
                    detail="[verification_abandoned] gave up",
                    bump_attempts=False,
                )
            )
        )
        row = await conn.fetchrow(
            "SELECT verify_state, verify_attempts, verify_detail "
            "FROM public.publish_task_accounts WHERE id = $1",
            seeded["row_id"],
        )
        assert row["verify_state"] == "abandoned"
        assert row["verify_attempts"] == 5
        assert "verification_abandoned" in row["verify_detail"]
    finally:
        await conn.execute(
            "DELETE FROM public.publish_tasks WHERE id = $1", seeded["task_id"]
        )


async def test_the_mirror_gate_query_executes_on_postgres(conn):
    """The correlated `array_agg` subquery in `_mirrored_open_stmt` is the one
    piece of new SQL that a unit test cannot vouch for — compiling is not
    running. Executed for real; the row set does not matter."""
    from app.workflows.publish_issue_mirror import _mirrored_open_stmt

    rows = await conn.fetch(_sql(_mirrored_open_stmt()))
    assert isinstance(rows, list)
