"""I1 regression pin (adversarial review, 2026-08-04): proves the
``is_enforced("resources")``-gated ``system_request_scope`` wrap added at
each of the 7 Phase A call sites is LOAD-BEARING in production, not
decorative.

Context: ``SCOPE_ENFORCE_RESOURCES`` DEFAULTS to false in code, but
production sets it TRUE via ``secrets/backend.env`` (outside this repo
tree — CLAUDE.md's 部署陷阱 on env overriding config.yml). Every read/write
comment added in this migration batch claiming "no-op today (flag off)"
was WRONG about production — this file exists so that claim can never be
silently wrong again: it exercises the REAL ``do_orm_execute`` choke point
(``app/db/scope.py``, registered globally on import) against the REAL
``Resources`` ORM model, with ``SCOPE_ENFORCE_RESOURCES`` patched True.

No ``INTEGRATION_DATABASE_URL`` needed — the choke point's fail-closed
raise happens from a pure Python statement-tree walk
(``_walk_scoped_refs``) BEFORE any SQL is compiled or sent to a database,
so an in-memory SQLite session (bound to NO schema at all) is enough:
dialect-independent by construction. ``Resources`` cannot actually be
``CREATE TABLE``'d on SQLite (it has a ``JSONB`` column SQLite's DDL
compiler rejects), so these tests don't try — they prove two things per
site's exact statement shape:

  (a) POSITIVE — wrapped in the real ``system_request_scope`` (SYSTEM
      scope: no injection, no raise), the statement clears the choke
      point and reaches real execution, failing only with
      ``OperationalError`` ("no such table") — proof it was never
      intercepted by ``UnscopedQueryError``.
  (b) COUNTERFACTUAL — with NO scope open at all (simulating "the wrap
      was deleted"), the exact same statement is intercepted and
      fail-closed raises ``UnscopedQueryError`` BEFORE reaching the DB —
      proof the wrap is what stands between this query and a 500 in
      production.

Each test's statement is copied from its production call site (see the
cross-reference in each test) — not imported, since the production
functions build the statement inline. Keep these in sync if the
production query shape changes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import String, and_, cast, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.scope as scope_module
from app.db.scope import UnscopedQueryError, system_request_scope
from app.models import (
    ParsedMedia,
    ResourceItems,
    Resources,
    ResourceVersions,
    TeamMembers,
    Teams,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# TeamMembers.user_id is a Uuid-typed column — SQLAlchemy's Uuid bind
# processor calls .hex on the bound value, so a plain str ("u1") blows up
# with AttributeError during parameter binding (masking the OperationalError
# these tests are actually checking for). Use a real UUID.
_UID = uuid.uuid4()


@pytest.fixture
async def sqlite_sessionmaker():
    """Real AsyncSession machinery bound to an in-memory SQLite engine with
    NO schema created — enough to exercise the real do_orm_execute event
    (see module docstring for why no schema is needed)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    sm = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture
def enforce_resources_on(monkeypatch):
    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)


async def _assert_load_bearing(sqlite_sessionmaker, stmt) -> None:
    """Shared (a)+(b) assertion pair for a SELECT/UPDATE statement that
    references Resources exactly like a gated call site does."""
    # (b) COUNTERFACTUAL — no ambient scope at all: fail-closed BEFORE any
    # DB round-trip (no "no such table" noise — the raise pre-empts it).
    async with sqlite_sessionmaker() as session:
        with pytest.raises(UnscopedQueryError):
            await session.execute(stmt)

    # (a) POSITIVE — real system_request_scope wrap: clears the choke
    # point, reaches real execution, fails ONLY on the throwaway DB having
    # no schema (never UnscopedQueryError).
    async with system_request_scope(reason="i1-regression-pin"):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(OperationalError):
                await session.execute(stmt)


# ── 1. app/main.py::_resolve_file_path — resources lookup ──────────────


async def test_main_resolve_file_path_resources_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/main.py:~412 — SELECT id, creator_id, file_path FROM resources
    WHERE id = :id."""
    stmt = select(Resources.id, Resources.creator_id, Resources.file_path).where(
        Resources.id == 1
    )
    await _assert_load_bearing(sqlite_sessionmaker, stmt)


# ── 2. app/api/media_slides_router.py::_resolve_album_location ─────────


async def test_media_slides_router_album_location_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/api/media_slides_router.py::_resolve_album_location — resources
    JOIN resource_versions ON current_version, WHERE media_id."""
    stmt = (
        select(ResourceVersions.file_path)
        .select_from(Resources)
        .join(
            ResourceVersions,
            and_(
                ResourceVersions.resource_id == Resources.id,
                ResourceVersions.version_number == Resources.current_version,
            ),
        )
        .where(Resources.media_id == 700)
        .limit(1)
    )
    await _assert_load_bearing(sqlite_sessionmaker, stmt)


# ── 3. app/repositories/resources_repository.py::list_accessible_for_user ──


async def test_resources_repository_list_accessible_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """resources_repository.py::list_accessible_for_user — resources JOIN
    resource_items, OUTER JOIN teams, team-membership IN-subquery."""
    id_text = cast(Resources.id, String)
    scope_id_text = cast(ResourceItems.scope_id, String)
    membership_ids = select(cast(TeamMembers.team_id, String)).where(
        TeamMembers.user_id == _UID
    )
    stmt = (
        select(id_text.label("id"), Resources.filename.label("name"))
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .outerjoin(Teams, Teams.id == ResourceItems.scope_id)
        .where(Resources.is_trashed.is_(False))
        .where(scope_id_text.in_(membership_ids))
    )
    await _assert_load_bearing(sqlite_sessionmaker, stmt)


# ── 4. app/services/ai/tools/resource_fetch_tool.py::_fetch_dispatch ───


async def test_resource_fetch_tool_access_check_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """resource_fetch_tool.py::_fetch_dispatch — resources JOIN
    resource_items, team-membership IN-subquery."""
    id_text = cast(Resources.id, String)
    scope_text = cast(ResourceItems.scope_id, String)
    membership_subq = select(cast(TeamMembers.team_id, String)).where(
        TeamMembers.user_id == _UID
    )
    stmt = (
        select(id_text.label("id"), Resources.mime_type.label("mime"))
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .where(id_text == "1")
        .where(Resources.is_trashed.is_(False))
        .where(scope_text.in_(membership_subq))
        .limit(1)
    )
    await _assert_load_bearing(sqlite_sessionmaker, stmt)


# ── 5. app/services/ai/chat/resource_ref_resolver.py::_fetch_accessible_meta ──


async def test_resource_ref_resolver_meta_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """resource_ref_resolver.py::_fetch_accessible_meta — resources JOIN
    resource_items, OUTER JOIN teams, id ANY(:ids), team-membership
    IN-subquery."""
    id_text = cast(Resources.id, String)
    scope_text = cast(ResourceItems.scope_id, String)
    membership_subq = select(cast(TeamMembers.team_id, String)).where(
        TeamMembers.user_id == _UID
    )
    stmt = (
        select(id_text.label("id"), Resources.filename.label("name"))
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .outerjoin(Teams, Teams.id == ResourceItems.scope_id)
        .where(id_text.in_(["1", "2"]))
        .where(Resources.is_trashed.is_(False))
        .where(scope_text.in_(membership_subq))
    )
    await _assert_load_bearing(sqlite_sessionmaker, stmt)


# ── 6. app/services/ai/chat/history_image_replay.py::_load_file_path_db ──


async def test_history_image_replay_file_path_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """history_image_replay.py::_load_file_path_db — resources JOIN
    resource_items, team-membership IN-subquery."""
    id_text = cast(Resources.id, String)
    scope_text = cast(ResourceItems.scope_id, String)
    membership_subq = select(cast(TeamMembers.team_id, String)).where(
        TeamMembers.user_id == _UID
    )
    stmt = (
        select(Resources.file_path)
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .where(id_text == "1")
        .where(Resources.is_trashed.is_(False))
        .where(scope_text.in_(membership_subq))
        .limit(1)
    )
    await _assert_load_bearing(sqlite_sessionmaker, stmt)


# ── 7. app/services/media/downloader/downloader.py::_repoint_album_resource_version ──


async def test_downloader_repoint_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """downloader.py::_repoint_album_resource_version — UPDATE
    resource_versions ... FROM resources (bulk Core DML referencing
    Resources only via the WHERE/FROM join, not the UPDATE target)."""
    stmt = (
        update(ResourceVersions)
        .where(ResourceVersions.resource_id == Resources.id)
        .where(ResourceVersions.resource_id == 42)
        .where(ResourceVersions.version_number == Resources.current_version)
        .where(ResourceVersions.file_path.is_not(None))
        .where(ResourceVersions.file_path.notlike("sb://%"))
        .values(file_path="sb://library/t5/album/42/")
    )
    await _assert_load_bearing(sqlite_sessionmaker, stmt)


# ── Sanity: an UNSCOPED table (ParsedMedia) is unaffected either way ────


async def test_unscoped_parsed_media_query_never_raises_unscoped_query_error(
    enforce_resources_on, sqlite_sessionmaker
):
    """Negative control: ParsedMedia carries no scope mixin, so even with
    SCOPE_ENFORCE_RESOURCES on and no scope open, it must NOT be treated
    as a scoped table — confirms the choke point is precise, not blanket."""
    stmt = select(ParsedMedia.download_path).where(ParsedMedia.id == 1)
    async with sqlite_sessionmaker() as session:
        with pytest.raises(OperationalError):
            await session.execute(stmt)
