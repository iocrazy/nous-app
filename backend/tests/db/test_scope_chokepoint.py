"""Integration tests for the app-layer scope choke point (app/db/scope.py).

Proves the fail-closed tenant-isolation mechanism against a REAL Postgres
using a TEST-ONLY scoped model + ephemeral table — never a production model
(the safety constraint: mixing a prod model in would break every existing
unscoped repo call).

Coverage:
  * user_session(scope): SELECT returns only rows owned by scope.user_id OR
    shared to a team in scope.team_ids (OR-combined multi-axis).
  * unset scope + SELECT on a scoped model → UnscopedQueryError (fail-closed).
  * system_session: SELECT returns ALL rows (no injection).
  * before_insert: stamps user_id from scope when unset; raises on mismatch.
  * a plain (non-scoped) model is unaffected by the event.
  * ContextVar isolation: scope set in one task does not leak to a sibling.

Setup (DSN gated, same as the other tests/db files):

    source /tmp/orm2_integration.env
    uv run pytest tests/db/test_scope_chokepoint.py -v

SKIPS cleanly (exit 0) when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import BigInteger, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.orm_base import TeamScoped, UserScoped
from app.db.scope import (
    Scope,
    UnscopedQueryError,
    current_scope,
    system_session,
    user_session,
)
from app.db.session import read_scope

# ── Module-level skip gate ──────────────────────────────────────────────
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Unique table names per worker process so parallel xdist workers don't stomp.
_PID = os.getpid()
_SCOPED_TABLE = f"_scope_chokepoint_test_{_PID}"
_PLAIN_TABLE = f"_scope_plain_test_{_PID}"


# ── TEST-ONLY mapped models (never a production table) ──────────────────
#
# These map onto a SEPARATE DeclarativeBase, NOT app.db.orm_base.Base, so the
# throwaway tables never land in the production Base.metadata (which would make
# test_schema_drift.py flag them as "missing from live schema"). The choke-point
# events key off the marker-mixin subclass check, independent of which Base the
# model uses — so isolation here does not weaken the test.


class _TestBase(DeclarativeBase):
    pass


class _ScopedRow(_TestBase, UserScoped, TeamScoped):
    """Throwaway model for the choke-point test: user + team axes.

    UserScoped.__tenant_user_col__ stays the default 'user_id'.
    """

    __tablename__ = _SCOPED_TABLE
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[str] = mapped_column(String, nullable=True)


class _PlainRow(_TestBase):
    """Plain mapped model with NO scope mixin — must be unaffected by the
    choke-point event (sanity check)."""

    __tablename__ = _PLAIN_TABLE
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payload: Mapped[str] = mapped_column(String, nullable=True)


# ── DSN availability fixture ────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping scope choke-point tests"
        )
    return _TEST_DSN


# ── Singleton reset + engine fixture (mirrors test_orm_session.py) ──────


@pytest.fixture
async def patched_engine(integration_db_url: str):
    import app.db.engine as db_engine
    import app.db.session as db_session

    db_engine._engine = None
    db_session._sessionmaker = None

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield

    try:
        await db_engine.dispose_engine()
    finally:
        db_engine._engine = None
        db_session._sessionmaker = None


# ── Ephemeral tables fixture ────────────────────────────────────────────


@pytest.fixture
async def seeded_tables(patched_engine):
    """Create both ephemeral tables, seed the scoped one, yield, then drop.

    Seeds three scoped rows for OR-combined assertions:
      - row owned by the test user (user_id=USER)         → visible to USER
      - row shared to USER's team (team_id=TEAM, other owner) → visible to USER
      - row owned by ANOTHER user, no shared team          → NOT visible
    """
    from sqlalchemy import text

    from app.db.engine import get_engine

    engine = get_engine()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {_SCOPED_TABLE} (
                    id      bigint PRIMARY KEY,
                    user_id bigint,
                    team_id bigint,
                    payload text
                )
                """
            )
        )
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {_PLAIN_TABLE} (
                    id      bigint PRIMARY KEY,
                    payload text
                )
                """
            )
        )

    ids = _Ids()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"INSERT INTO {_SCOPED_TABLE} (id, user_id, team_id, payload) "
                "VALUES (:id, :uid, :tid, :p)"
            ),
            [
                # owned by the test user
                {"id": ids.own, "uid": ids.user, "tid": None, "p": "own"},
                # shared to the test user's team, owned by someone else
                {"id": ids.team, "uid": ids.other, "tid": ids.team_id, "p": "team"},
                # owned by another user, not shared → invisible
                {"id": ids.other_row, "uid": ids.other, "tid": None, "p": "other"},
            ],
        )

    yield ids

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_SCOPED_TABLE}"))
        await conn.execute(text(f"DROP TABLE IF EXISTS {_PLAIN_TABLE}"))


class _Ids:
    """Collision-resistant bigint identifiers for one test run."""

    def __init__(self) -> None:
        self.user = _pk()
        self.other = _pk()
        self.team_id = _pk()
        self.own = _pk()
        self.team = _pk()
        self.other_row = _pk()


def _pk() -> int:
    """Collision-resistant positive bigint (high 63 bits of UUID4)."""
    return uuid.uuid4().int >> 65


# ── Tests ───────────────────────────────────────────────────────────────


async def test_user_session_injects_or_combined_filter(seeded_tables: _Ids):
    """user_session(scope): SELECT returns only the owned row + the team-shared
    row; the other user's unshared row is filtered out."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user, team_ids=frozenset({ids.team_id}))

    async with user_session(scope) as session:
        result = await session.execute(select(_ScopedRow))
        rows = result.scalars().all()

    got_ids = {r.id for r in rows}
    payloads = {r.payload for r in rows}

    assert got_ids == {
        ids.own,
        ids.team,
    }, f"expected own+team rows {{{ids.own}, {ids.team}}}, got {got_ids}"
    assert payloads == {"own", "team"}
    assert ids.other_row not in got_ids, "another user's row leaked through scope"


async def test_user_session_empty_teams_only_owned(seeded_tables: _Ids):
    """With empty team_ids, the team axis matches nothing → only the owned row
    comes back (verifies empty-frozenset axis handling)."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user, team_ids=frozenset())

    async with user_session(scope) as session:
        result = await session.execute(select(_ScopedRow))
        rows = result.scalars().all()

    got_ids = {r.id for r in rows}
    assert got_ids == {
        ids.own
    }, f"empty team_ids should yield only the owned row, got {got_ids}"


async def test_unset_scope_raises_unscoped_query_error(seeded_tables: _Ids):
    """A SELECT on a scoped model with NO scope established must fail-closed."""
    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with read_scope() as session:
            await session.execute(select(_ScopedRow))


async def test_system_session_returns_all_rows(seeded_tables: _Ids):
    """system_session: no injection → every row is visible."""
    ids = seeded_tables
    async with system_session("test: cross-user read") as session:
        result = await session.execute(select(_ScopedRow))
        rows = result.scalars().all()

    got_ids = {r.id for r in rows}
    assert {
        ids.own,
        ids.team,
        ids.other_row,
    } <= got_ids, f"system_session should see all seeded rows, got {got_ids}"


async def test_before_insert_stamps_user_id_when_unset(seeded_tables: _Ids):
    """Inserting a scoped row with user_id unset stamps it from the scope."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)
    new_id = _pk()

    async with user_session(scope) as session:
        row = _ScopedRow(id=new_id, payload="stamp_me")  # user_id left unset
        session.add(row)
        await session.flush()
        # after flush the before_insert event has fired
        assert (
            row.user_id == ids.user
        ), f"before_insert did not stamp user_id (got {row.user_id!r})"

    # Re-read under the same scope: the stamped row is now visible (proves it
    # was persisted with the scope's user_id).
    async with user_session(scope) as session:
        result = await session.execute(
            select(_ScopedRow).where(_ScopedRow.id == new_id)
        )
        fetched = result.scalar_one()
    assert fetched.user_id == ids.user
    assert fetched.payload == "stamp_me"


async def test_before_insert_rejects_foreign_user_id(seeded_tables: _Ids):
    """Inserting with a user_id different from the scope must raise."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match="another user"):
        async with user_session(scope) as session:
            row = _ScopedRow(id=_pk(), user_id=ids.other, payload="foreign")
            session.add(row)
            await session.flush()


async def test_plain_model_unaffected_by_event(seeded_tables: _Ids):
    """A non-scoped model is NOT touched by the choke point: it is readable
    with no scope established and returns all its rows."""
    plain_id = _pk()
    # Insert a plain row with no scope (must NOT raise — plain model).
    async with read_scope() as session:
        session.add(_PlainRow(id=plain_id, payload="plain"))
        await session.commit()

    async with read_scope() as session:
        result = await session.execute(
            select(_PlainRow).where(_PlainRow.id == plain_id)
        )
        row = result.scalar_one()

    assert row.id == plain_id
    assert row.payload == "plain"


async def test_contextvar_scope_isolation_across_tasks(seeded_tables: _Ids):
    """A scope SET inside one task does not leak back to a concurrent sibling.

    ContextVar semantics: a task copies its parent's context at creation, but
    a ``.set()`` performed *after* that copy (i.e. inside user_session) is
    private to the task doing the set. Here two tasks each open their own
    user_session concurrently with different user_ids; neither sees the
    other's scope, and the launching task's scope stays None throughout.
    """
    ids = seeded_tables
    scope_a = Scope(user_id=ids.user)
    scope_b = Scope(user_id=ids.other)
    barrier = asyncio.Event()
    seen: dict[str, object] = {}

    async def worker(name: str, scope: Scope) -> None:
        async with user_session(scope):
            # Record this task's own view, then wait for both to be inside
            # their session simultaneously before reading again.
            seen[f"{name}_self"] = current_scope()
            barrier_count[0] += 1
            if barrier_count[0] == 2:
                barrier.set()
            await barrier.wait()
            # Even with both sessions concurrently active, each task sees only
            # its own scope — the other's .set() did not leak in.
            seen[f"{name}_after"] = current_scope()

    barrier_count = [0]
    await asyncio.gather(worker("a", scope_a), worker("b", scope_b))

    assert seen["a_self"] == scope_a
    assert seen["b_self"] == scope_b
    assert seen["a_after"] == scope_a, "task a's scope was clobbered by task b"
    assert seen["b_after"] == scope_b, "task b's scope was clobbered by task a"
    # The launching task never set a scope → still None.
    assert current_scope() is None
