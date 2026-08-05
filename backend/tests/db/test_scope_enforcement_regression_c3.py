"""C3 regression pin (Phase C task 3, 2026-08-05): proves the scope wraps
added at the 5 newly-migrated ``resources``-touching tenant statements are
LOAD-BEARING in production, not decorative — same contract as
``test_scope_enforcement_regression_c1.py`` (Phase C task 1), extended to
cover this batch's 5 sites across two files:

  - ``app/repositories/tags_repository.py::_get_tag_counts_fallback`` — a
    real per-user ``request_scope(Scope(user_id=...))`` wrap (NOT
    ``system_request_scope``): the caller (``tags_router.get_tag_statistics``)
    opens no ambient scope of its own, and ``user_id`` here IS the
    ``creator_id`` the query already filters on, so the correct fix is a
    real user boundary, not a cross-tenant one.
  - ``app/repositories/resources_repository.py::_resource_ids_for_platforms``
    — a SELECT with NO per-owner filter (system_request_scope; see the
    method's own security-audit-note docstring).
  - ``...::validate_scope_image_ids`` — a SELECT scoped by
    ``resource_items.scope_id`` (team/project), not ``creator_id``
    (system_request_scope).
  - ``...::restore_folder_cascade`` / ``...::trash_folder_cascade`` — bulk
    Core UPDATEs on ``Resources`` scoped by folder membership, not
    ``creator_id`` (system_request_scope; bulk DML on a scoped model is
    additionally FORBIDDEN under any real user Scope — see
    ``_forbid_scoped_bulk_dml``).

Context: ``SCOPE_ENFORCE_RESOURCES`` DEFAULTS to false in code, but
production sets it TRUE via ``secrets/backend.env`` (CLAUDE.md 部署陷阱).
Every scope wrap added in this migration batch is what stands between the
statement and a fail-closed 500 (or, for the two bulk UPDATEs, a
completely broken folder restore/trash for every user) once the flag is on.

No ``INTEGRATION_DATABASE_URL`` needed — same dialect-independent in-memory
SQLite rationale as C1/I1 (the choke point's fail-closed raise happens from
a pure Python statement-tree walk before any SQL is compiled or sent to a
database). Statements below are reconstructed INDEPENDENTLY here (not
imported from the production module) so this file is a true reverse
self-check of the production shape, not a tautological re-assertion of
whatever the implementation happens to build.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.scope import Scope, UnscopedQueryError, request_scope, system_request_scope
from app.models import ResourceItems, Resources

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# A real UUID — a plain str trips SQLite's Uuid bind_processor
# (AttributeError instead of the expected OperationalError; see C1's module
# docstring). Purely a SQLite-test-harness accommodation — real Postgres
# accepts a plain str natively.
_UID = uuid.uuid4()


@pytest.fixture
async def sqlite_sessionmaker():
    """Real AsyncSession machinery bound to an in-memory SQLite engine with
    NO schema created — enough to exercise the real do_orm_execute event."""
    engine = create_async_engine("sqlite+aiosqlite://")
    sm = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture
def enforce_resources_on(monkeypatch):
    import app.db.scope as scope_module

    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)


async def _assert_select_load_bearing_via_system_scope(
    sqlite_sessionmaker, stmt
) -> None:
    """(a)+(b) pair for a SELECT whose correct wrap is ``system_request_scope``
    (no per-owner filter by design)."""
    async with sqlite_sessionmaker() as session:
        with pytest.raises(UnscopedQueryError):
            await session.execute(stmt)

    async with system_request_scope(reason="c3-regression-pin"):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(OperationalError):
                await session.execute(stmt)


async def _assert_select_load_bearing_via_user_scope(sqlite_sessionmaker, stmt) -> None:
    """(a)+(b) pair for a SELECT whose correct wrap is a REAL per-user
    ``request_scope(Scope(user_id=_UID))`` (the query already filters on
    that same identity — a system-wide wrap would be semantically wrong
    here, see ``_get_tag_counts_fallback``'s docstring)."""
    async with sqlite_sessionmaker() as session:
        with pytest.raises(UnscopedQueryError):
            await session.execute(stmt)

    async with request_scope(Scope(user_id=_UID)):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(OperationalError):
                await session.execute(stmt)


async def _assert_write_load_bearing(sqlite_sessionmaker, stmt) -> None:
    """(a)+(b)+(c) triple for a bulk Core UPDATE on Resources — mirrors C1's
    helper: forbidden under both no-scope and a REAL user Scope, only
    SYSTEM may issue it."""
    async with sqlite_sessionmaker() as session:
        with pytest.raises(UnscopedQueryError):
            await session.execute(stmt)

    async with request_scope(Scope(user_id=_UID)):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(UnscopedQueryError):
                await session.execute(stmt)

    async with system_request_scope(reason="c3-regression-pin"):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(OperationalError):
                await session.execute(stmt)


# ── 1. tags_repository.py::_get_tag_counts_fallback ──────────────────────


async def test_tag_counts_fallback_resources_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """SELECT id FROM resources WHERE creator_id = :uid — real per-user
    request_scope, not system_request_scope (see module docstring).

    Uses the real ``uuid.UUID`` object (not ``str(_UID)``, which is what the
    production code binds) for both the explicit filter and the ambient
    Scope — SQLite's ``Uuid`` type's bind_processor calls ``.hex`` on the
    bound value, which only a real ``uuid.UUID`` has; a plain ``str`` raises
    ``AttributeError`` instead of the expected ``OperationalError`` (a
    SQLite-test-harness quirk, not a production concern — see C1's module
    docstring for the full explanation)."""
    stmt = select(Resources.id).where(Resources.creator_id == _UID)
    await _assert_select_load_bearing_via_user_scope(sqlite_sessionmaker, stmt)


# ── 2. resources_repository.py::_resource_ids_for_platforms ─────────────


async def test_resource_ids_for_platforms_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """SELECT id FROM resources WHERE media_id IN (...) — no per-owner
    filter by design (see the method's security-audit-note docstring);
    system_request_scope is the correct wrap."""
    stmt = select(Resources.id).where(Resources.media_id.in_([1, 2]))
    await _assert_select_load_bearing_via_system_scope(sqlite_sessionmaker, stmt)


# ── 3. resources_repository.py::validate_scope_image_ids ─────────────────


async def test_validate_scope_image_ids_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """SELECT DISTINCT resources.id JOIN resource_items ON ... WHERE
    resource_items.scope_id = :sid AND mime_type LIKE 'image/%' — scoped by
    team/project membership, not creator_id; system_request_scope."""
    stmt = (
        select(Resources.id)
        .distinct()
        .select_from(Resources)
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .where(Resources.id.in_([1, 2]))
        .where(ResourceItems.scope_id == 5)
        .where(Resources.mime_type.like("image/%"))
    )
    await _assert_select_load_bearing_via_system_scope(sqlite_sessionmaker, stmt)


# ── 4. resources_repository.py::restore_folder_cascade ───────────────────


async def test_restore_folder_cascade_resources_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """Bulk UPDATE resources SET is_trashed=false WHERE id IN (subquery on
    resource_items.folder_id) — folder-membership scoped, not creator_id;
    bulk DML on a scoped model is additionally forbidden under any real
    user Scope, so only SYSTEM may run it."""
    stmt = (
        update(Resources)
        .where(Resources.is_trashed.is_(True))
        .where(
            Resources.id.in_(
                select(ResourceItems.resource_id).where(
                    ResourceItems.folder_id.in_([1, 2, 3])
                )
            )
        )
        .values(is_trashed=False, trashed_at=None)
    )
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)


# ── 5. resources_repository.py::trash_folder_cascade ──────────────────────


async def test_trash_folder_cascade_resources_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """Bulk UPDATE ... FROM resource_items (correlated snapshot columns) —
    same folder-membership scoping and bulk-DML-forbidden-under-real-scope
    rationale as restore_folder_cascade."""
    stmt = (
        update(Resources)
        .where(Resources.id == ResourceItems.resource_id)
        .where(ResourceItems.folder_id.in_([1, 2, 3]))
        .where(Resources.is_trashed.is_(False))
        .values(
            is_trashed=True,
            trashed_at=func.now(),
            last_folder_id=ResourceItems.folder_id,
            last_library_id=ResourceItems.library_id,
            last_scope_id=ResourceItems.scope_id,
        )
    )
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)
