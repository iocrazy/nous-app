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
  * bulk/Core DML (update/delete/insert) on a scoped table under a real Scope
    fail-closed (forbid-by-default — load-then-modify is the safe path); the
    same statements succeed under system_session and fail-closed under no scope.
  * the sanctioned instance-flush write path (session.add / dirty update /
    session.delete) still works under a real scope (forbid must NOT over-reach).
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
from sqlalchemy import BigInteger, String, delete, insert, select, text, update
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, mapped_column

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
# UNSCOPED media-like table with a media_id/scoped_id FK into _ScopedRow. Used
# to exercise the indirect-scope JOIN/subquery leak cases (the parsed_media →
# resources pattern: media has no owner col, scope comes from the resources JOIN).
_MEDIA_TABLE = f"_scope_media_test_{_PID}"


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


class _PlainMedia(_TestBase):
    """UNSCOPED media-like model (no scope mixin) carrying a ``scoped_id`` FK
    into ``_ScopedRow`` — mirrors ``parsed_media`` whose tenancy is indirect via
    ``resources.media_id``. Reading it via a JOIN/subquery on the scoped table is
    the C2 leak shape: the scoped table is referenced but NOT in the columns
    clause, so the loader criteria never reaches it → must fail-closed."""

    __tablename__ = _MEDIA_TABLE
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scoped_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
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
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {_MEDIA_TABLE} (
                    id        bigint PRIMARY KEY,
                    scoped_id bigint,
                    payload   text
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
        # Two media rows, each pointing at a scoped row (one owned, one foreign).
        await conn.execute(
            text(
                f"INSERT INTO {_MEDIA_TABLE} (id, scoped_id, payload) "
                "VALUES (:id, :sid, :p)"
            ),
            [
                {"id": ids.media_own, "sid": ids.own, "p": "media_own"},
                {"id": ids.media_other, "sid": ids.other_row, "p": "media_other"},
            ],
        )

    yield ids

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_SCOPED_TABLE}"))
        await conn.execute(text(f"DROP TABLE IF EXISTS {_PLAIN_TABLE}"))
        await conn.execute(text(f"DROP TABLE IF EXISTS {_MEDIA_TABLE}"))


class _Ids:
    """Collision-resistant bigint identifiers for one test run."""

    def __init__(self) -> None:
        self.user = _pk()
        self.other = _pk()
        self.team_id = _pk()
        self.own = _pk()
        self.team = _pk()
        self.other_row = _pk()
        # media rows pointing at the owned + the foreign scoped row
        self.media_own = _pk()
        self.media_other = _pk()


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
    assert got_ids == {ids.own}, (
        f"empty team_ids should yield only the owned row, got {got_ids}"
    )


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
        assert row.user_id == ids.user, (
            f"before_insert did not stamp user_id (got {row.user_id!r})"
        )

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


# ── Write-path forbid: bulk/Core DML under a user scope (C1 / C3) ────────
#
# A real Scope must FORBID bulk UPDATE/DELETE and Core/bulk INSERT on a scoped
# table — these statements run through do_orm_execute but cannot be safely
# WHERE-injected/owner-stamped, so we fail-closed and force load-then-modify or
# an explicit system_session. SYSTEM is trusted to write its own WHERE/owner.


async def test_bulk_update_under_user_scope_raises(seeded_tables: _Ids):
    """C1: a Core bulk UPDATE on a scoped table under a real scope must raise —
    otherwise it would mutate every user's rows with no tenant filter."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user, team_ids=frozenset({ids.team_id}))

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with user_session(scope) as session:
            await session.execute(update(_ScopedRow).values(payload="hijacked"))


async def test_orm_enabled_update_synchronize_fetch_under_scope_raises(
    seeded_tables: _Ids,
):
    """C1: the ORM-enabled update().where().execution_options(
    synchronize_session='fetch') form must also raise (still a Core-style
    statement, no per-row owner check)."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with user_session(scope) as session:
            await session.execute(
                update(_ScopedRow)
                .where(_ScopedRow.id == ids.other_row)
                .values(payload="hijacked")
                .execution_options(synchronize_session="fetch")
            )


async def test_bulk_delete_under_user_scope_raises(seeded_tables: _Ids):
    """C1: a Core bulk DELETE on a scoped table under a real scope must raise —
    otherwise it would delete other users' rows."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with user_session(scope) as session:
            await session.execute(delete(_ScopedRow))


async def test_core_insert_under_user_scope_raises(seeded_tables: _Ids):
    """C3: a Core insert().values(...) bypasses before_insert owner-stamping, so
    under a real scope it must raise (a user could otherwise insert rows owned
    by another user)."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with user_session(scope) as session:
            await session.execute(
                insert(_ScopedRow).values(
                    id=_pk(), user_id=ids.other, payload="foreign"
                )
            )


async def test_bulk_insert_under_user_scope_raises(seeded_tables: _Ids):
    """C3: bulk insert (insert() + list of param dicts) is also a Core insert
    that bypasses owner-stamping → must raise under a real scope."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with user_session(scope) as session:
            await session.execute(
                insert(_ScopedRow),
                [
                    {"id": _pk(), "user_id": ids.other, "payload": "a"},
                    {"id": _pk(), "user_id": ids.other, "payload": "b"},
                ],
            )


async def test_system_session_allows_bulk_dml(seeded_tables: _Ids):
    """system_session is trusted: the SAME update/delete/insert statements that
    raise under a user scope must SUCCEED (system code writes its own WHERE)."""
    ids = seeded_tables
    new_id = _pk()

    async with system_session("test: cross-user bulk DML") as session:
        # Core insert succeeds.
        await session.execute(
            insert(_ScopedRow).values(
                id=new_id, user_id=ids.other, payload="sys_insert"
            )
        )
        # Bulk update succeeds (touches that row).
        await session.execute(
            update(_ScopedRow)
            .where(_ScopedRow.id == new_id)
            .values(payload="sys_update")
        )
        # Bulk delete of the row we just made succeeds.
        await session.execute(delete(_ScopedRow).where(_ScopedRow.id == new_id))

    # Confirm the row is gone (all three statements actually ran).
    async with system_session("test: verify") as session:
        result = await session.execute(
            select(_ScopedRow).where(_ScopedRow.id == new_id)
        )
        assert result.scalars().all() == []


async def test_no_scope_bulk_dml_raises(seeded_tables: _Ids):
    """Fail-closed: bulk UPDATE/DELETE/INSERT on a scoped table with NO scope
    established must also raise (same posture as the unset-scope SELECT)."""
    ids = seeded_tables

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with read_scope() as session:
            await session.execute(update(_ScopedRow).values(payload="x"))

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with read_scope() as session:
            await session.execute(delete(_ScopedRow))

    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with read_scope() as session:
            await session.execute(
                insert(_ScopedRow).values(id=_pk(), user_id=ids.user, payload="x")
            )


# ── Regression: the sanctioned instance-flush path must STILL work ───────


async def test_instance_update_under_scope_still_works(seeded_tables: _Ids):
    """REGRESSION: load a row under scope, mutate an attribute, commit. This is
    the sanctioned safe path (governed by the scoped SELECT that loaded it +
    the UoW flush) — it must NOT be caught by the bulk-DML forbid."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    async with user_session(scope) as session:
        result = await session.execute(
            select(_ScopedRow).where(_ScopedRow.id == ids.own)
        )
        row = result.scalar_one()
        row.payload = "mutated"  # dirty-instance update → flush, not do_orm_execute

    # Re-read: the mutation persisted.
    async with user_session(scope) as session:
        result = await session.execute(
            select(_ScopedRow).where(_ScopedRow.id == ids.own)
        )
        assert result.scalar_one().payload == "mutated"


async def test_instance_delete_under_scope_still_works(seeded_tables: _Ids):
    """REGRESSION: session.delete(loaded_instance) is the sanctioned delete
    path (governed by the scoped SELECT that loaded it) → must NOT raise."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)
    new_id = _pk()

    # First insert a row owned by the scope via the sanctioned add path.
    async with user_session(scope) as session:
        session.add(_ScopedRow(id=new_id, payload="to_delete"))

    # Load it, then delete the instance.
    async with user_session(scope) as session:
        result = await session.execute(
            select(_ScopedRow).where(_ScopedRow.id == new_id)
        )
        await session.delete(result.scalar_one())

    # Confirm gone.
    async with user_session(scope) as session:
        result = await session.execute(
            select(_ScopedRow).where(_ScopedRow.id == new_id)
        )
        assert result.scalars().all() == []


async def test_plain_model_bulk_dml_unaffected(seeded_tables: _Ids):
    """REGRESSION: bulk UPDATE/INSERT on a NON-scoped model must NOT be caught
    by the forbid even with no scope set — the choke point ignores it."""
    plain_id = _pk()
    async with read_scope() as session:
        # Core insert on a plain model: no raise.
        await session.execute(
            insert(_PlainRow).values(id=plain_id, payload="plain_insert")
        )
        await session.commit()

    async with read_scope() as session:
        # Bulk update on a plain model: no raise.
        await session.execute(
            update(_PlainRow)
            .where(_PlainRow.id == plain_id)
            .values(payload="plain_update")
        )
        await session.commit()

    async with read_scope() as session:
        result = await session.execute(
            select(_PlainRow).where(_PlainRow.id == plain_id)
        )
        assert result.scalar_one().payload == "plain_update"


# ── C2 — full-statement traversal: scoped table reached via JOIN / subquery /
#    from_statement / writable CTE must FAIL-CLOSED (it cannot be injected) ─────
#
# These are the indirect-scope leak shapes the columns-clause-only injection
# missed: the scoped table is referenced but NOT a queried entity, so
# with_loader_criteria never reaches it. Policy = raise (accepted cost; the
# owning repo restructures to an injectable shape or uses system_session).


async def test_join_only_scoped_table_raises(seeded_tables: _Ids):
    """C2: select(_PlainMedia).join(_ScopedRow) — scoped table is join-only, not
    in the columns clause → must raise (otherwise unscoped media leaks every
    user's rows). This is the parsed_media-via-resources-JOIN shape."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user, team_ids=frozenset({ids.team_id}))

    with pytest.raises(UnscopedQueryError, match=_SCOPED_TABLE):
        async with user_session(scope) as session:
            await session.execute(
                select(_PlainMedia).join(
                    _ScopedRow, _PlainMedia.scoped_id == _ScopedRow.id
                )
            )


async def test_outerjoin_scoped_table_raises(seeded_tables: _Ids):
    """C2: outerjoin form is the same leak — scoped table referenced, not
    injectable → raise."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match=_SCOPED_TABLE):
        async with user_session(scope) as session:
            await session.execute(
                select(_PlainMedia).outerjoin(
                    _ScopedRow, _PlainMedia.scoped_id == _ScopedRow.id
                )
            )


async def test_in_subquery_scoped_table_raises(seeded_tables: _Ids):
    """C2: scoped entity hidden in an IN-subquery — referenced but not in the
    outer columns clause → raise."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match=_SCOPED_TABLE):
        async with user_session(scope) as session:
            await session.execute(
                select(_PlainMedia).where(
                    _PlainMedia.scoped_id.in_(select(_ScopedRow.id))
                )
            )


async def test_from_statement_raw_text_scoped_raises(seeded_tables: _Ids):
    """C2: select(_ScopedRow).from_statement(text(...)) — the scoped mapper IS
    in all_mappers, but with_loader_criteria is a silent no-op against raw
    from_statement text (is_from_statement=True), so it would leak → raise."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    with pytest.raises(UnscopedQueryError, match=_SCOPED_TABLE):
        async with user_session(scope) as session:
            await session.execute(
                select(_ScopedRow).from_statement(
                    text(f"SELECT * FROM {_SCOPED_TABLE}")
                )
            )


async def test_writable_cte_dml_bypass_is_pinned(seeded_tables: _Ids):
    """C2: a DML statement nested in a CTE under a top-level SELECT reports as a
    SELECT (is_select True, is_update False), so it slips past the bulk-DML
    write-forbid; the columns-clause injection never sees the CTE's UPDATE
    target. The full-statement traversal now reaches into the CTE element and
    finds the scoped table → must raise (closing the cross-tenant WRITE bypass).

    (Was xfail-strict before the traversal fix; now a normal must-PASS test.)
    """
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    cte = (
        update(_ScopedRow)
        .where(_ScopedRow.id == ids.other_row)  # victim row owned by another user
        .values(payload="HIJACKED")
        .returning(_ScopedRow.id)
        .cte("u")
    )

    with pytest.raises(UnscopedQueryError, match=_SCOPED_TABLE):
        async with user_session(scope) as session:
            await session.execute(select(cte.c.id))

    # And prove the victim row was NOT mutated (the statement never ran).
    async with system_session("test: verify victim untouched") as session:
        result = await session.execute(
            select(_ScopedRow).where(_ScopedRow.id == ids.other_row)
        )
        assert result.scalar_one().payload == "other", "victim row was hijacked!"


async def test_no_scope_join_scoped_table_raises(seeded_tables: _Ids):
    """C2 + fail-closed: with NO scope, a JOIN onto a scoped table must raise too
    (the None-scope fail-closed extends to the whole traversal set, not just the
    columns clause)."""
    with pytest.raises(UnscopedQueryError, match=_SCOPED_TABLE):
        async with read_scope() as session:
            await session.execute(
                select(_PlainMedia).join(
                    _ScopedRow, _PlainMedia.scoped_id == _ScopedRow.id
                )
            )


async def test_no_scope_in_subquery_scoped_table_raises(seeded_tables: _Ids):
    """C2 + fail-closed: with NO scope, a scoped table in an IN-subquery must
    raise."""
    with pytest.raises(UnscopedQueryError, match=_SCOPED_TABLE):
        async with read_scope() as session:
            await session.execute(
                select(_PlainMedia).where(
                    _PlainMedia.scoped_id.in_(select(_ScopedRow.id))
                )
            )


# ── GREEN-PIN: genuinely-injectable shapes must still INJECT (not raise) ─────
#
# These reference the scoped entity IN THE COLUMNS CLAUSE, so the loader
# criteria reaches them. They must keep returning owned+team rows only — the
# traversal must NOT over-reach and raise on them.


async def test_aliased_scoped_entity_still_injects(seeded_tables: _Ids):
    """GREEN-PIN: aliased(_ScopedRow) is in the columns clause (covered) → the
    filter is injected via include_aliases=True → owned+team only, no raise."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user, team_ids=frozenset({ids.team_id}))

    al = aliased(_ScopedRow)
    async with user_session(scope) as session:
        result = await session.execute(select(al))
        rows = result.scalars().all()

    got = {r.id for r in rows}
    assert got == {ids.own, ids.team}, f"aliased injection leaked/over-filtered: {got}"


async def test_system_session_join_sees_all(seeded_tables: _Ids):
    """GREEN-PIN: under system_session the same join that RAISES under a user
    scope must SUCCEED and see all media (no injection, no raise)."""
    ids = seeded_tables
    async with system_session("test: cross-user join") as session:
        result = await session.execute(
            select(_PlainMedia).join(_ScopedRow, _PlainMedia.scoped_id == _ScopedRow.id)
        )
        media = result.scalars().all()

    got = {m.id for m in media}
    assert {ids.media_own, ids.media_other} <= got, f"system join missed rows: {got}"


async def test_plain_media_alone_untouched(seeded_tables: _Ids):
    """GREEN-PIN / inertness guard: select(_PlainMedia) alone references NO
    scoped table → no scope needed, no raise, ALL media rows returned. Proves
    the traversal does not false-positive on unscoped-only statements (the vast
    majority of prod queries)."""
    ids = seeded_tables
    async with read_scope() as session:
        result = await session.execute(select(_PlainMedia))
        media = result.scalars().all()

    got = {m.id for m in media}
    assert {ids.media_own, ids.media_other} <= got, f"plain media untouched: {got}"


# ── session.get(): PK-based load is the most common access pattern ───────────
#
# session.get(Model, pk) emits a SELECT-by-PK that flows through do_orm_execute
# as a real SELECT (is_select=True) with the scoped mapper in the columns clause,
# so the existing tenant injection covers it: a foreign-PK get returns None
# (NOT the other tenant's row), an owned-PK get returns the row, no-scope raises,
# and system_session gets any row by PK. These pin that behaviour so a future
# change to the enforcement gate cannot silently reopen the PK-get leak.


async def test_get_foreign_pk_returns_none(seeded_tables: _Ids):
    """A get() for ANOTHER user's PK under a real scope must return None (the
    injected tenant filter makes the SELECT-by-PK match nothing) — NOT leak the
    foreign row. PK-get is the single most common access pattern in the repos."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user, team_ids=frozenset({ids.team_id}))

    async with user_session(scope) as session:
        got = await session.get(_ScopedRow, ids.other_row)

    assert got is None, f"foreign-PK get leaked another user's row: {got!r}"


async def test_get_owned_pk_returns_row(seeded_tables: _Ids):
    """A get() for the scope's OWN PK returns the row (injection must not
    over-filter the legitimately-owned row)."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    async with user_session(scope) as session:
        got = await session.get(_ScopedRow, ids.own)

    assert got is not None and got.payload == "own", f"owned-PK get failed: {got!r}"


async def test_get_foreign_pk_after_owned_load_still_none(seeded_tables: _Ids):
    """Identity-map edge: loading the owned row first must NOT pull the foreign
    row into the session, so a subsequent foreign-PK get() in the same session
    still returns None (the scoped query never loaded the foreign row)."""
    ids = seeded_tables
    scope = Scope(user_id=ids.user)

    async with user_session(scope) as session:
        await session.execute(select(_ScopedRow).where(_ScopedRow.id == ids.own))
        got = await session.get(_ScopedRow, ids.other_row)

    assert got is None, f"foreign-PK get leaked after owned load: {got!r}"


async def test_get_no_scope_on_scoped_raises(seeded_tables: _Ids):
    """Fail-closed: a get() on a scoped model with NO scope established must
    raise (the SELECT-by-PK touches the scoped table with no scope)."""
    ids = seeded_tables
    with pytest.raises(UnscopedQueryError, match="_ScopedRow"):
        async with read_scope() as session:
            await session.get(_ScopedRow, ids.own)


async def test_get_system_session_any_pk(seeded_tables: _Ids):
    """system_session: get() returns any row by PK (no injection)."""
    ids = seeded_tables
    async with system_session("test: cross-user get") as session:
        got = await session.get(_ScopedRow, ids.other_row)

    assert got is not None and got.payload == "other", f"system get failed: {got!r}"


async def test_get_plain_model_unaffected(seeded_tables: _Ids):
    """A get() on the UNSCOPED _PlainMedia is unaffected: no scope needed, the
    row comes back by PK with no injection or raise."""
    ids = seeded_tables
    async with read_scope() as session:
        got = await session.get(_PlainMedia, ids.media_own)

    assert got is not None and got.payload == "media_own", f"plain get failed: {got!r}"
