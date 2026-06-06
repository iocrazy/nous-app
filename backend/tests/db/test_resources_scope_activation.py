"""Integration tests for the FLAG-GATED activation of the scope choke point on
the REAL ``Resources`` model (epic A / task A1).

Unlike ``test_scope_chokepoint.py`` (which uses throwaway ``_TestBase`` models
that default to ENFORCED), this file exercises the production ``Resources`` table
(``creator_id`` UUID owner) through the ``SCOPE_ENFORCE_RESOURCES`` enforcement
gate. It proves:

  * FLAG OFF (prod default): ``resources`` behaves byte-for-byte like an
    UNSCOPED table — ``select(Resources)`` with no scope returns rows and does
    NOT raise; a Core bulk UPDATE with no scope does not raise. Zero behaviour
    change while the ``UserScoped`` mixin is on the model.
  * FLAG ON + ambient user scope (``request_scope``): reads return only
    ``creator_id == scope.user_id`` rows (foreign user excluded end-to-end);
    ``session.get(Resources, foreign_pk)`` → None; a cross-user bulk UPDATE →
    ``UnscopedQueryError`` (write-forbid).
  * FLAG ON + NO scope: ``select(Resources)`` → ``UnscopedQueryError``
    (fail-closed).
  * FLAG ON + the leak classes on REAL resources: OUTER-join → raise; a
    correlated ``count(*)`` subquery over resources → raise; an INNER join
    injects + excludes the foreign row.

The ``Scope.user_id`` here is a UUID STRING (the Supabase auth ``sub``), because
``resources.creator_id`` is a ``uuid`` column — NOT a bigint. The choke point's
``creator_id == scope.user_id`` binds the UUID string against the ``uuid`` column.

Seeds two real resources rows owned by two distinct UUIDs (A = the "test user",
B = a foreign user) and cleans them up by id. Never touches another user's data
beyond the throwaway rows it inserts.

Setup (DSN gated, same as the other tests/db files):

    source /tmp/orm2_integration.env
    uv run pytest tests/db/test_resources_scope_activation.py -v

SKIPS cleanly (exit 0) when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text, update

from app.core import scope_dep
from app.core.deps import AuthContext
from app.db import scope as scope_mod
from app.db.scope import (
    Scope,
    UnscopedQueryError,
    current_scope,
    request_scope,
    system_request_scope,
)
from app.db.session import read_scope
from app.models.media import ParsedMedia, Resources

# ── Module-level skip gate ──────────────────────────────────────────────
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping resources-scope tests")
    return _TEST_DSN


# ── Singleton reset + engine fixture (mirrors test_scope_chokepoint.py) ──


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


# ── Flag toggles. Patch BOTH the config object and the scope module's bound
#    ``settings`` reference (they are the same object, but patch the name the
#    predicate actually reads) so the enforcement predicate flips. ───────────


@pytest.fixture
def enforce_on():
    with patch.object(scope_mod.settings, "SCOPE_ENFORCE_RESOURCES", True):
        yield


@pytest.fixture
def enforce_off():
    with patch.object(scope_mod.settings, "SCOPE_ENFORCE_RESOURCES", False):
        yield


# ── Seeded resources rows (two distinct UUID owners) ────────────────────


class _Ids:
    def __init__(self) -> None:
        self.user_a = str(uuid.uuid4())  # the "test user"
        self.user_b = str(uuid.uuid4())  # a foreign user
        self.res_a = _pk()  # owned by A
        self.res_b = _pk()  # owned by B
        self.media_a = _pk()  # parsed_media linked to res_a (A's)
        self.media_b = _pk()  # parsed_media linked to res_b (B's)


def _pk() -> int:
    """Collision-resistant positive bigint (high 63 bits of UUID4)."""
    return uuid.uuid4().int >> 65


@pytest.fixture
async def seeded_resources(patched_engine):
    """Insert two throwaway ``auth.users`` (A, B) + two ``resources`` rows (one
    owned by each), yield ids, clean up in FK order.

    ``resources.creator_id`` is FK → ``auth.users(id)``, so the two owners must
    exist first. Inserts are raw SQL under an engine connection (NOT the ORM) so
    the choke point never governs the seeding regardless of the enforcement flag.
    """
    from app.db.engine import get_engine

    engine = get_engine()
    ids = _Ids()

    async with engine.begin() as conn:
        # Throwaway owners (only id is required; booleans default false).
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (CAST(:uid AS uuid))"),
            [{"uid": ids.user_a}, {"uid": ids.user_b}],
        )
        # Two parsed_media rows (unscoped table — tenancy is INDIRECT via the
        # resources.media_id FK). Mirrors the real parsed_media→resources shape
        # the A4 indirect-scope check targets.
        await conn.execute(
            text(
                "INSERT INTO parsed_media (id, platform_id, original_url) "
                "VALUES (:id, :pid, :url)"
            ),
            [
                {"id": ids.media_a, "pid": f"pid_{ids.media_a}", "url": "http://a"},
                {"id": ids.media_b, "pid": f"pid_{ids.media_b}", "url": "http://b"},
            ],
        )
        await conn.execute(
            text(
                "INSERT INTO resources (id, creator_id, source_type, filename, "
                "media_id) VALUES (:id, CAST(:cid AS uuid), 'web', :fn, :mid)"
            ),
            [
                {
                    "id": ids.res_a,
                    "cid": ids.user_a,
                    "fn": "owned_by_a",
                    "mid": ids.media_a,
                },
                {
                    "id": ids.res_b,
                    "cid": ids.user_b,
                    "fn": "owned_by_b",
                    "mid": ids.media_b,
                },
            ],
        )

    yield ids

    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM resources WHERE id = ANY(:ids)"),
            {"ids": [ids.res_a, ids.res_b]},
        )
        await conn.execute(
            text("DELETE FROM parsed_media WHERE id = ANY(:ids)"),
            {"ids": [ids.media_a, ids.media_b]},
        )
        await conn.execute(
            text("DELETE FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [ids.user_a, ids.user_b]},
        )


# ── FLAG OFF: byte-for-byte legacy (no injection, no raise) ─────────────


async def test_flag_off_select_no_scope_returns_rows_no_raise(
    seeded_resources: _Ids, enforce_off
):
    """FLAG OFF: ``select(Resources)`` with NO scope established does NOT raise and
    returns BOTH seeded rows (cross-user visible) — proving resources is inert /
    unscoped while the mixin is on but the flag is off."""
    ids = seeded_resources
    async with read_scope() as session:
        result = await session.execute(
            select(Resources).where(Resources.id.in_([ids.res_a, ids.res_b]))
        )
        rows = result.scalars().all()

    got = {r.id for r in rows}
    assert got == {ids.res_a, ids.res_b}, f"flag-off must be unscoped, got {got}"


async def test_flag_off_bulk_update_no_scope_no_raise(
    seeded_resources: _Ids, enforce_off
):
    """FLAG OFF: a Core bulk UPDATE on resources with NO scope does NOT raise
    (the write-forbid is inert). Scoped to the seeded rows so it mutates nothing
    else; rolled back implicitly by not committing... but write_scope commits, so
    we restore the value explicitly is unnecessary — we update filename to itself."""
    ids = seeded_resources
    from app.db.session import write_scope

    async with write_scope() as session:
        await session.execute(
            update(Resources)
            .where(Resources.id == ids.res_a)
            .values(filename="owned_by_a")  # no-op value
        )
    # No exception == pass (legacy/inert write path).


async def test_flag_off_select_under_ambient_scope_unfiltered(
    seeded_resources: _Ids, enforce_off
):
    """FLAG OFF: even WITH an ambient user scope set, resources is not enforced →
    no injection → both rows still come back (the scope is simply ignored for an
    un-enforced table)."""
    ids = seeded_resources
    async with request_scope(Scope(user_id=ids.user_a)):
        async with read_scope() as session:
            result = await session.execute(
                select(Resources).where(Resources.id.in_([ids.res_a, ids.res_b]))
            )
            rows = result.scalars().all()

    got = {r.id for r in rows}
    assert got == {ids.res_a, ids.res_b}, f"flag-off ignores scope, got {got}"


async def test_flag_off_orm_insert_no_scope_no_raise(
    seeded_resources: _Ids, enforce_off
):
    """FLAG OFF: an ORM-instance insert (``session.add(Resources(...))`` + flush)
    with NO scope established must NOT raise — the ``before_insert`` owner-stamp
    must be gated by ``_is_enforced`` so a non-enforced scoped-by-class table
    plain-inserts with ``creator_id`` used AS-GIVEN (byte-for-byte legacy).

    (Write-side analogue of the SELECT enforced-set gate. Against 984f8ac6 the
    ungated ``before_insert`` would raise UnscopedQueryError on the None scope.)
    """
    ids = seeded_resources
    from app.db.engine import get_engine
    from app.db.session import write_scope

    new_id = _pk()
    try:
        async with write_scope() as session:
            # creator_id set explicitly to a DIFFERENT existing user (user_b):
            # with the gate OFF this must be inserted as-is, NOT stamped/asserted
            # against any scope (there is none) — proving the legacy plain path.
            session.add(
                Resources(
                    id=new_id,
                    creator_id=ids.user_b,
                    source_type="web",
                    filename="orm_insert_flag_off",
                )
            )
            await session.flush()  # before_insert fires here — must NOT raise

        # The row persisted with creator_id used as-given (no stamp/assert).
        async with system_request_scope("test: verify flag-off orm insert"):
            async with read_scope() as session:
                got = await session.get(Resources, new_id)
        assert got is not None, "flag-off ORM insert did not persist"
        assert (
            str(got.creator_id) == ids.user_b
        ), f"creator_id was not used as-given: {got.creator_id!r}"
    finally:
        # Explicit cleanup — this row is outside the seeded_resources teardown set.
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM resources WHERE id = :id"), {"id": new_id}
            )


# ── FLAG ON + ambient user scope: own-only reads, foreign excluded ──────


async def test_flag_on_select_returns_own_rows_only(seeded_resources: _Ids, enforce_on):
    """FLAG ON: ``select(Resources)`` under A's ambient scope returns A's row and
    EXCLUDES B's row end-to-end."""
    ids = seeded_resources
    async with request_scope(Scope(user_id=ids.user_a)):
        async with read_scope() as session:
            result = await session.execute(
                select(Resources).where(Resources.id.in_([ids.res_a, ids.res_b]))
            )
            rows = result.scalars().all()

    got = {r.id for r in rows}
    assert ids.res_a in got, f"owned resource missing: {got}"
    assert ids.res_b not in got, f"FOREIGN resource LEAKED through scope: {got}"


async def test_flag_on_get_foreign_pk_returns_none(seeded_resources: _Ids, enforce_on):
    """FLAG ON: ``session.get(Resources, B_pk)`` under A's scope returns None (the
    injected ``creator_id == A`` filter makes the PK-load match nothing)."""
    ids = seeded_resources
    async with request_scope(Scope(user_id=ids.user_a)):
        async with read_scope() as session:
            got = await session.get(Resources, ids.res_b)

    assert got is None, f"foreign-PK get leaked another user's resource: {got!r}"


async def test_flag_on_get_owned_pk_returns_row(seeded_resources: _Ids, enforce_on):
    """FLAG ON: ``session.get(Resources, A_pk)`` under A's scope returns the row
    (injection must not over-filter the legitimately-owned row)."""
    ids = seeded_resources
    async with request_scope(Scope(user_id=ids.user_a)):
        async with read_scope() as session:
            got = await session.get(Resources, ids.res_a)

    assert got is not None and got.id == ids.res_a, f"owned-PK get failed: {got!r}"


async def test_flag_on_cross_user_bulk_update_raises(
    seeded_resources: _Ids, enforce_on
):
    """FLAG ON: a Core bulk UPDATE on resources under a user scope must raise
    (cannot be safely tenant-filtered — load-then-modify is the safe path)."""
    ids = seeded_resources
    from app.db.session import write_scope

    # The bulk-DML forbid names the offending model by CLASS name (``Resources``).
    with pytest.raises(UnscopedQueryError, match="Resources"):
        async with request_scope(Scope(user_id=ids.user_a)):
            async with write_scope() as session:
                await session.execute(update(Resources).values(filename="hijacked"))


async def test_flag_on_system_request_scope_sees_all(
    seeded_resources: _Ids, enforce_on
):
    """FLAG ON: under ``system_request_scope`` (ambient SYSTEM) resources reads see
    BOTH rows (no injection, no raise) — the session-less system entry."""
    ids = seeded_resources
    async with system_request_scope("test: cross-user resources read"):
        async with read_scope() as session:
            result = await session.execute(
                select(Resources).where(Resources.id.in_([ids.res_a, ids.res_b]))
            )
            rows = result.scalars().all()

    got = {r.id for r in rows}
    assert got == {ids.res_a, ids.res_b}, f"system scope must see all: {got}"


# ── FLAG ON + NO scope: fail-closed ─────────────────────────────────────


async def test_flag_on_no_scope_select_raises(seeded_resources: _Ids, enforce_on):
    """FLAG ON: ``select(Resources)`` with NO scope established must fail-closed."""
    ids = seeded_resources
    with pytest.raises(UnscopedQueryError, match="resources"):
        async with read_scope() as session:
            await session.execute(select(Resources).where(Resources.id == ids.res_a))


async def test_flag_on_no_scope_get_raises(seeded_resources: _Ids, enforce_on):
    """FLAG ON: ``session.get(Resources, pk)`` with NO scope must fail-closed."""
    ids = seeded_resources
    with pytest.raises(UnscopedQueryError, match="resources"):
        async with read_scope() as session:
            await session.get(Resources, ids.res_a)


# ── FLAG ON + the leak classes on REAL resources ────────────────────────


async def test_flag_on_media_inner_join_resources_injects_excludes_foreign(
    seeded_resources: _Ids, enforce_on
):
    """FLAG ON / INJECT shape (the motivating parsed_media→resources case):
    ``select(ParsedMedia).join(Resources)`` INJECTS the tenant filter into the
    scoped resources table → only A's media (joined to A's resource) returns; B's
    media (joined to B's resource) is EXCLUDED end-to-end."""
    ids = seeded_resources
    async with request_scope(Scope(user_id=ids.user_a)):
        async with read_scope() as session:
            result = await session.execute(
                select(ParsedMedia)
                .join(Resources, Resources.media_id == ParsedMedia.id)
                .where(ParsedMedia.id.in_([ids.media_a, ids.media_b]))
            )
            media = result.scalars().all()

    got = {m.id for m in media}
    assert ids.media_a in got, f"owned media missing from join: {got}"
    assert ids.media_b not in got, f"FOREIGN media LEAKED through join: {got}"


async def test_flag_on_outerjoin_resources_raises(seeded_resources: _Ids, enforce_on):
    """FLAG ON / RESIDUE: an OUTER JOIN onto resources lands the tenant predicate
    in the row-preserving ON clause (the foreign row would survive) → fail-closed
    RAISE."""
    ids = seeded_resources
    with pytest.raises(UnscopedQueryError, match="resources"):
        async with request_scope(Scope(user_id=ids.user_a)):
            async with read_scope() as session:
                await session.execute(
                    select(ParsedMedia).outerjoin(
                        Resources, Resources.media_id == ParsedMedia.id
                    )
                )


async def test_flag_on_count_star_correlated_subquery_raises(
    seeded_resources: _Ids, enforce_on
):
    """FLAG ON / LEAK→RAISE: a correlated ``count(*)`` scalar subquery over
    resources (correlated to the UNSCOPED parsed_media outer) projects NO scoped
    column, so ``with_loader_criteria`` has no anchor and silently no-ops → only
    DENY-BY-DEFAULT (the compile-probe finds no rendered tenant predicate) catches
    it → fail-closed RAISE (closing the existence-count leak on real resources)."""
    ids = seeded_resources
    sub = (
        select(func.count())
        .where(Resources.media_id == ParsedMedia.id)
        .scalar_subquery()
    )
    with pytest.raises(UnscopedQueryError, match="resources"):
        async with request_scope(Scope(user_id=ids.user_a)):
            async with read_scope() as session:
                await session.execute(
                    select(ParsedMedia.id, sub.label("cnt")).where(
                        ParsedMedia.id.in_([ids.media_a, ids.media_b])
                    )
                )


# ── The scoped_request FastAPI dependency sets the ambient scope ────────


async def test_scoped_request_dependency_sets_and_resets_scope():
    """The ``scoped_request`` generator dependency sets ``current_scope()`` to a
    user scope carrying ``AuthContext.user_id`` during the yield and resets it to
    None afterwards. (No DB needed — pure ContextVar behaviour.)"""
    uid = str(uuid.uuid4())
    auth = AuthContext(user_id=uid, auth_type="jwt")

    assert current_scope() is None, "precondition: no ambient scope"

    gen = scope_dep.scoped_request(auth=auth)
    await gen.__anext__()  # enter — sets the ambient scope
    try:
        scope = current_scope()
        assert isinstance(scope, Scope), f"dependency did not set a Scope: {scope!r}"
        assert scope.user_id == uid, f"scope user_id mismatch: {scope.user_id!r}"
        assert scope.team_ids == frozenset()
        assert scope.project_ids == frozenset()
    finally:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()  # exit — resets the ambient scope

    assert current_scope() is None, "ambient scope leaked after dependency exit"
