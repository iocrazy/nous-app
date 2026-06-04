"""Integration tests for the SQLAlchemy 2.0 async session scopes.

Validates the three scopes in app/db/session.py against a REAL Postgres:

  - write_scope():  commits on clean exit; rolls back on raise.
  - unit_of_work(): two writes are atomic (one txn); commits both or rolls
                    back both.
  - read_scope():   returns rows as plain dicts; sees in-flight UoW writes.

Uses an ephemeral table ``_orm_session_test`` created and dropped per test
run — never touches app tables.

Setup: requires INTEGRATION_DATABASE_URL pointing at a PG instance (the dev
Supabase stack is available via SSH tunnel at 127.0.0.1:55434).

    source /tmp/orm2_integration.env
    uv run pytest tests/db/test_orm_session.py -v

The file SKIPS cleanly (exit 0) when INTEGRATION_DATABASE_URL is unset so
the unit-only CI job is not affected.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

# ── Module-level skip gate ──────────────────────────────────────────────
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Unique table name per worker process so parallel pytest-xdist workers don't
# CREATE/DROP the same table and stomp each other.
_TABLE = f"_orm_session_test_{os.getpid()}"


# ── DSN availability fixture ────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping ORM session integration tests"
        )
    return _TEST_DSN


# ── Singleton reset + engine fixture ───────────────────────────────────


@pytest.fixture
async def patched_engine(integration_db_url: str):
    """Point BOTH singletons (engine + sessionmaker) at the test DSN.

    Mirrors the ``patched_pool`` pattern from test_asyncpg_repos.py:
      1. Reset the engine singleton to None.
      2. Patch settings.SUPAVISOR_DATABASE_URL so get_engine() rebuilds
         against the test database.
      3. Reset the sessionmaker singleton so get_sessionmaker() rebuilds
         against the new engine.
      4. Teardown: dispose engine + reset both singletons to None so the
         next test/fixture gets a clean slate.
    """
    import app.db.engine as db_engine
    import app.db.session as db_session

    db_engine._engine = None
    db_session._sessionmaker = None

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield

    # Reset the singletons even if dispose_engine() raises — otherwise a stale
    # engine bound to the test DSN would corrupt later DB tests in this session.
    try:
        await db_engine.dispose_engine()
    finally:
        db_engine._engine = None
        db_session._sessionmaker = None


# ── Ephemeral test table fixture ────────────────────────────────────────


@pytest.fixture
async def test_table(patched_engine):
    """Create the ephemeral test table in the test DB.

    Depends on ``patched_engine`` only — which itself depends on
    ``integration_db_url``, so the skip-gate propagates transitively.

    Created with autocommit (engine.begin() on a raw connection) so the DDL
    is visible to all later transactions in the same test.  Dropped on
    teardown — even on test failure.

    Columns:
      id          bigint PRIMARY KEY    — exercises bigint round-trip
      label       text                  — simple text payload
      payload     jsonb                 — exercises jsonb column type
      created_at  timestamptz default now() — server_default path
    """
    from app.db.engine import get_engine

    engine = get_engine()

    # CREATE — autocommit via engine.begin()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    id         bigint      PRIMARY KEY,
                    label      text        NOT NULL,
                    payload    jsonb,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )

    yield _TABLE

    # DROP on teardown
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLE}"))


# ── Helper: generate a unique bigint PK ────────────────────────────────


def _pk() -> int:
    """Return a collision-resistant positive bigint (high 63 bits of UUID4)."""
    return uuid.uuid4().int >> 65


# ── Tests ───────────────────────────────────────────────────────────────


async def test_write_scope_commits(test_table: str):
    """write_scope() must commit on clean exit.

    After the ``write_scope`` block closes without raising, a SEPARATE fresh
    session opened via ``read_scope()`` must see the inserted row.
    """
    from app.db.session import read_scope, write_scope

    row_id = _pk()

    async with write_scope() as session:
        await session.execute(
            text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
            {"id": row_id, "label": "write_scope_commit_test"},
        )

    # Verify with a fresh, independent session.
    async with read_scope() as session:
        result = await session.execute(
            text(f"SELECT id, label FROM {_TABLE} WHERE id = :id"),
            {"id": row_id},
        )
        rows = [dict(r) for r in result.mappings().all()]

    assert len(rows) == 1, "write_scope did not commit: row not visible in new session"
    assert rows[0]["label"] == "write_scope_commit_test"


async def test_write_scope_rollback_on_raise(test_table: str):
    """write_scope() must roll back when the body raises.

    The row must NOT be visible in a subsequent fresh session.
    """
    from app.db.session import read_scope, write_scope

    row_id = _pk()

    with pytest.raises(RuntimeError, match="intentional rollback"):
        async with write_scope() as session:
            await session.execute(
                text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                {"id": row_id, "label": "should_be_rolled_back"},
            )
            raise RuntimeError("intentional rollback")

    # Row must be absent.
    async with read_scope() as session:
        result = await session.execute(
            text(f"SELECT id FROM {_TABLE} WHERE id = :id"),
            {"id": row_id},
        )
        rows = result.mappings().all()

    assert len(rows) == 0, "write_scope failed to roll back: row is visible after raise"


async def test_unit_of_work_commit_path(test_table: str):
    """unit_of_work() must commit all writes when no exception is raised.

    Two write_scope() calls nested inside one unit_of_work() share the same
    transaction; both rows must be visible after.
    """
    from app.db.session import unit_of_work, write_scope

    id_a = _pk()
    id_b = _pk()

    async with unit_of_work():
        async with write_scope() as session:
            await session.execute(
                text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                {"id": id_a, "label": "uow_commit_a"},
            )
        async with write_scope() as session:
            await session.execute(
                text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                {"id": id_b, "label": "uow_commit_b"},
            )

    # Both rows must be visible in a fresh session.
    from app.db.session import read_scope

    async with read_scope() as session:
        result = await session.execute(
            text(f"SELECT id, label FROM {_TABLE} WHERE id = ANY(:ids)"),
            {"ids": [id_a, id_b]},
        )
        rows = {r["id"]: r["label"] for r in result.mappings().all()}

    assert id_a in rows, "uow_commit: first row missing after commit"
    assert id_b in rows, "uow_commit: second row missing after commit"
    assert rows[id_a] == "uow_commit_a"
    assert rows[id_b] == "uow_commit_b"


async def test_unit_of_work_atomicity_on_raise(test_table: str):
    """unit_of_work() must roll back ALL writes when any write raises.

    First write succeeds; second write then raises. Because both are inside
    the SAME unit_of_work() transaction, NEITHER row must appear in a fresh
    session after the UoW block exits with the exception.
    """
    from app.db.session import read_scope, unit_of_work, write_scope

    id_a = _pk()
    id_b = _pk()

    with pytest.raises(RuntimeError, match="uow intentional rollback"):
        async with unit_of_work():
            async with write_scope() as session:
                await session.execute(
                    text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                    {"id": id_a, "label": "uow_rollback_a"},
                )
            async with write_scope() as session:
                await session.execute(
                    text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                    {"id": id_b, "label": "uow_rollback_b"},
                )
                raise RuntimeError("uow intentional rollback")

    # Both rows must be absent: atomicity means the first write is also gone.
    async with read_scope() as session:
        result = await session.execute(
            text(f"SELECT id FROM {_TABLE} WHERE id = ANY(:ids)"),
            {"ids": [id_a, id_b]},
        )
        rows = result.mappings().all()

    assert len(rows) == 0, (
        f"unit_of_work did not roll back atomically: "
        f"found {len(rows)} row(s) after raise (expected 0)"
    )


async def test_read_scope_returns_plain_dicts(test_table: str):
    """read_scope() must yield sessions whose query results can be consumed
    as plain dicts via .mappings() → dict(row).

    Also verifies that a row committed by write_scope() is readable
    immediately after via read_scope() in a fresh session.
    """
    from app.db.session import read_scope, write_scope

    row_id = _pk()

    async with write_scope() as session:
        await session.execute(
            text(
                f"INSERT INTO {_TABLE} (id, label, payload)"
                " VALUES (:id, :label, CAST(:payload AS jsonb))"
            ),
            {
                "id": row_id,
                "label": "dict_test",
                "payload": '{"answer": 42, "nested": {"ok": true}}',
            },
        )

    async with read_scope() as session:
        result = await session.execute(
            text(f"SELECT id, label, payload, created_at FROM {_TABLE} WHERE id = :id"),
            {"id": row_id},
        )
        rows = [dict(r) for r in result.mappings().all()]

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict), f"Expected dict, got {type(row).__name__}"
    assert row["label"] == "dict_test"
    assert row["id"] == row_id
    # created_at server_default should have been set by Postgres
    assert row["created_at"] is not None, "server_default created_at was not set"
    # jsonb round-trip: SQLAlchemy deserialises jsonb to a Python dict
    assert isinstance(
        row["payload"], dict
    ), f"jsonb column should deserialise to dict, got {type(row['payload']).__name__}"
    assert row["payload"]["answer"] == 42


async def test_read_scope_sees_inflight_uow_writes(test_table: str):
    """read_scope() nested inside unit_of_work() must see the UoW's
    uncommitted writes (same transaction, no isolation boundary).

    This is the dirty-read-within-own-txn behaviour: the reader joins the
    ambient UoW session via the _request_session contextvar.
    """
    from app.db.session import read_scope, unit_of_work, write_scope

    row_id = _pk()

    async with unit_of_work():
        # Write the row but do NOT commit yet (we're still inside the UoW).
        async with write_scope() as session:
            await session.execute(
                text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                {"id": row_id, "label": "inflight_visibility"},
            )

        # read_scope() inside the same UoW must join the same session/txn.
        async with read_scope() as session:
            result = await session.execute(
                text(f"SELECT id, label FROM {_TABLE} WHERE id = :id"),
                {"id": row_id},
            )
            rows = [dict(r) for r in result.mappings().all()]

    assert len(rows) == 1, (
        "read_scope inside unit_of_work did not see the in-flight write "
        "(expected same-session visibility before commit)"
    )
    assert rows[0]["label"] == "inflight_visibility"


async def test_nested_unit_of_work_requires_new(test_table: str):
    """Nesting unit_of_work() inside an active UoW = REQUIRES_NEW.

    The inner UoW opens a SEPARATE transaction that commits independently;
    it is NOT rolled back when the OUTER transaction rolls back. Proven via
    DB state (no get_ambient_session() helper exists):

      - outer UoW writes row A (joins the outer txn via write_scope)
      - nested UoW writes row B + exits cleanly → commits independently
      - outer UoW then raises → outer txn (with row A) rolls back

    After everything: row B present (inner committed), row A absent (outer
    rolled back). If nesting shared one transaction, B would also be gone.
    """
    from app.db.session import read_scope, unit_of_work, write_scope

    id_a = _pk()
    id_b = _pk()

    with pytest.raises(RuntimeError, match="outer intentional rollback"):
        async with unit_of_work():
            # Row A joins the OUTER transaction.
            async with write_scope() as session:
                await session.execute(
                    text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                    {"id": id_a, "label": "nested_outer_a"},
                )

            # Nested UoW = REQUIRES_NEW: row B commits in its own txn on clean
            # exit, independent of the outer transaction's fate.
            async with unit_of_work():
                async with write_scope() as session:
                    await session.execute(
                        text(f"INSERT INTO {_TABLE} (id, label) VALUES (:id, :label)"),
                        {"id": id_b, "label": "nested_inner_b"},
                    )

            # Now blow up the OUTER transaction → row A must roll back.
            raise RuntimeError("outer intentional rollback")

    # Fresh session: B survives (inner committed), A is gone (outer rolled back).
    async with read_scope() as session:
        result = await session.execute(
            text(f"SELECT id, label FROM {_TABLE} WHERE id = ANY(:ids)"),
            {"ids": [id_a, id_b]},
        )
        found = {r["id"]: r["label"] for r in result.mappings().all()}

    assert id_b in found, (
        "nested unit_of_work did NOT commit independently: row B missing "
        "(REQUIRES_NEW semantics broken — inner shared the outer txn)"
    )
    assert found[id_b] == "nested_inner_b"
    assert id_a not in found, (
        "outer unit_of_work did NOT roll back: row A is present after the "
        "outer raise (nesting wrongly shares one transaction)"
    )
