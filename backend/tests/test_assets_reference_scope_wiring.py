"""Pin the ambient SYSTEM-``Scope`` wiring on the assets reference read (P2 T4).

``AssetRelationsRepository.resource_media_rows`` is the ONLY ``Resources`` touch
on the assets router, and that router binds no tenant scope (no
``ScopedRequestDep``, no router-level ``dependencies=``). ``Resources`` carries
``UserScoped(creator_id)`` and the ``do_orm_execute`` choke point is registered
class-wide on ``Session``, so it governs a plain ``read_scope()`` too.

``SCOPE_ENFORCE_RESOURCES`` defaults to FALSE in code but production sets it
TRUE via ``secrets/backend.env`` (CLAUDE.md 部署陷阱: env overrides
config.yml). Without the ``system_request_scope`` wrap, the choke point sees a
scoped table with no ambient scope and fail-closed raises
``UnscopedQueryError`` — an unhandled 500 (the router catches ``AssetError``,
not this) on every ``POST /assets/{id}/generate-slot`` whose asset has a file
attached, i.e. every run the reference feature exists for. The preview endpoint
keeps working, so the observable shape is "preview lists 3 references, the run
500s".

The unit suite structurally cannot see this: ``FakeRelationsRepo`` is a dict
lookup and the flag is off by default. Hence this file, in the shape of
``test_sweeper_scope_wiring.py`` / ``test_cover_frames_scope.py``.

Three independent guards, any one of which must fail on its own regression:

  A. **the wrap exists and is SYSTEM** while the query runs, and leaks nothing.
  B. **flag off ⇒ no scope opened** — the byte-for-byte legacy path stays legacy.
  C. **positive/negative control on the REAL choke point** — proof that the
     guard is live in this process, that an unscoped SELECT on ``Resources``
     really raises, and that SYSTEM is what makes it pass. Without C, A is only
     "we set a ContextVar" and would still pass if the choke point's contract
     changed underneath it.

Pure-process: no Supabase, no network. C uses an in-memory SQLite session — the
raise happens in the event handler, BEFORE any SQL is emitted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.repositories.asset_relations_repository as repo_mod
from app.db.scope import SYSTEM, UnscopedQueryError, current_scope, system_request_scope
from app.models import Resources

REFS = [727145299382534146, 727145299382534147]
# What the real caller (``AssetsService._REFERENCE_READ_REASON``) passes.
REASON = "assets-generate-slot: resolve reference media paths"


class _FakeSession:
    """Captures the ambient scope at the moment the SELECT is executed."""

    def __init__(self, seen: dict):
        self._seen = seen

    async def execute(self, stmt):
        self._seen["scope"] = current_scope()

        class _R:
            @staticmethod
            def mappings():
                class _M:
                    @staticmethod
                    def all():
                        return []

                return _M()

        return _R()


def _patch_read_scope(monkeypatch, seen: dict):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_read_scope():
        yield _FakeSession(seen)

    monkeypatch.setattr(repo_mod, "read_scope", _fake_read_scope)


# ── A. the wrap exists, is SYSTEM, and does not leak ───────────────────────


@pytest.mark.asyncio
async def test_reference_read_runs_under_system_scope_when_enforced(monkeypatch):
    seen: dict = {}
    _patch_read_scope(monkeypatch, seen)
    monkeypatch.setattr(repo_mod, "is_enforced", lambda table: True)

    await repo_mod.AssetRelationsRepository().resource_media_rows(
        REFS, system_reason=REASON
    )

    assert seen.get("scope") is SYSTEM, (
        f"ambient scope was {seen.get('scope')!r} during resource_media_rows, "
        "expected SYSTEM — system_request_scope wiring regression. In "
        "production (SCOPE_ENFORCE_RESOURCES=true) this is a 500 on every "
        "generate-slot run whose asset has a file attached."
    )
    assert current_scope() is None, "ambient scope leaked out of resource_media_rows"


@pytest.mark.asyncio
async def test_the_wrap_is_asked_about_resources_specifically(monkeypatch):
    """``is_enforced`` is per-table; asking about the wrong one would read a
    flag that has nothing to do with the table being queried."""
    seen: dict = {}
    asked: list[str] = []
    _patch_read_scope(monkeypatch, seen)

    def _spy(table):
        asked.append(table)
        return True

    monkeypatch.setattr(repo_mod, "is_enforced", _spy)
    await repo_mod.AssetRelationsRepository().resource_media_rows(
        REFS, system_reason=REASON
    )
    assert asked == ["resources"]


@pytest.mark.asyncio
async def test_the_audit_reason_is_the_callers_not_a_hardcoded_one(monkeypatch):
    """``system_reason`` is required keyword-only for a reason: the audit line
    belongs to whoever decided the cross-user read was legitimate. Hardcoding
    it in the repo would file a second caller's read under THIS caller's
    justification — a true-looking audit line about the wrong access."""
    from contextlib import asynccontextmanager

    seen: dict = {}
    reasons: list[str] = []
    _patch_read_scope(monkeypatch, seen)
    monkeypatch.setattr(repo_mod, "is_enforced", lambda table: True)

    @asynccontextmanager
    async def _spy(reason):
        reasons.append(reason)
        async with system_request_scope(reason=reason):
            yield

    monkeypatch.setattr(repo_mod, "system_request_scope", _spy)

    await repo_mod.AssetRelationsRepository().resource_media_rows(
        REFS, system_reason="some-other-caller: a different justification"
    )

    assert reasons == ["some-other-caller: a different justification"]
    assert seen.get("scope") is SYSTEM


# ── B. flag off stays byte-for-byte legacy ─────────────────────────────────


@pytest.mark.asyncio
async def test_no_scope_is_opened_when_enforcement_is_off(monkeypatch):
    seen: dict = {}
    _patch_read_scope(monkeypatch, seen)
    monkeypatch.setattr(repo_mod, "is_enforced", lambda table: False)

    await repo_mod.AssetRelationsRepository().resource_media_rows(
        REFS, system_reason=REASON
    )

    assert seen.get("scope") is None, (
        "an ambient scope was opened with enforcement off — the gate exists to "
        "keep that path unchanged"
    )


@pytest.mark.asyncio
async def test_an_empty_reference_list_never_touches_the_database(monkeypatch):
    seen: dict = {}
    _patch_read_scope(monkeypatch, seen)
    monkeypatch.setattr(repo_mod, "is_enforced", lambda table: True)

    empty = await repo_mod.AssetRelationsRepository().resource_media_rows(
        [], system_reason=REASON
    )
    assert empty == {}
    assert "scope" not in seen


# ── C. the real choke point: negative and positive control ─────────────────


@pytest.fixture
async def sqlite_session():
    """A REAL ``AsyncSession``. The choke point is registered class-wide on
    ``Session``, so it fires here exactly as it does on the production engine —
    and it raises before any SQL reaches the database."""
    engine = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_unscoped_resources_select_really_raises(sqlite_session, monkeypatch):
    """NEGATIVE control — without this, the SYSTEM assertion above would pass
    even if the choke point had stopped enforcing anything."""
    monkeypatch.setattr("app.db.scope._is_enforced", lambda table: True)
    with pytest.raises(UnscopedQueryError):
        await sqlite_session.execute(select(Resources.id).where(Resources.id == 1))


@pytest.mark.asyncio
async def test_system_scope_gets_the_same_select_past_the_choke_point(
    sqlite_session, monkeypatch
):
    """POSITIVE control — under SYSTEM the statement gets through the guard and
    fails only on the (deliberately absent) SQLite table, i.e. the guard is no
    longer what stops it."""
    monkeypatch.setattr("app.db.scope._is_enforced", lambda table: True)
    async with system_request_scope(reason="test: assets reference read"):
        with pytest.raises(OperationalError) as exc:
            await sqlite_session.execute(select(Resources.id).where(Resources.id == 1))
    assert "no such table" in str(exc.value)


@pytest.mark.asyncio
async def test_the_choke_point_discriminates_by_table(sqlite_session, monkeypatch):
    """CONTROL for the negative control: the guard raises for SCOPED tables
    specifically, not for every statement. Without this, the raise above would
    also be produced by a choke point that had gone blanket-deny, and the
    SYSTEM wrap would be credited with fixing something it did not."""
    from app.models import AssetFiles  # plain Base — no scope mixin

    monkeypatch.setattr("app.db.scope._is_enforced", lambda table: True)
    with pytest.raises(OperationalError) as exc:
        await sqlite_session.execute(
            select(AssetFiles.asset_id).where(AssetFiles.asset_id == 1)
        )
    assert "no such table" in str(exc.value), "expected the DB to be what stops it"
