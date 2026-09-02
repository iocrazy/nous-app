"""Pin the ambient SYSTEM-``Scope`` wiring on the CANVAS resource reference read.

Sibling of ``test_assets_reference_scope_wiring.py``, for the second caller of
the same choke point. ``Resources`` carries ``UserScoped(creator_id)`` and the
``do_orm_execute`` guard is registered class-wide on ``Session``, so a plain
``read_scope()`` is governed too. ``SCOPE_ENFORCE_RESOURCES`` is FALSE in code
and TRUE in production (``secrets/backend.env`` overrides ``config.yml``), so a
missing wrap is invisible here and a 500 there — on every canvas run whose
prompt has an asset reference wired in, i.e. every run this bridge exists for.

The unit suite for the bridge itself (``test_canvas_resource_reference_bridge``)
structurally cannot see this: it fakes the repository, so no ``Resources``
statement is ever built. Hence this file.

Four guards, each of which must fail on its own regression:

  A. the read really goes through ``resource_media_rows`` — the ONE place that
     carries the wrap. A hand-rolled ``select(Resources…)`` inside the bridge
     would pass every behavioural test and fail in production only.
  B. the wrap is SYSTEM while the query runs, and leaks nothing.
  C. flag off ⇒ no scope opened; the legacy path stays byte-for-byte legacy.
  D. positive/negative control on the REAL choke point — proof the guard is
     live in this process and that SYSTEM is what gets past it. Without D, B is
     only "we set a ContextVar".

Pure-process: no Supabase, no network. D uses in-memory SQLite — the raise
happens in the event handler, BEFORE any SQL is emitted.
"""

from __future__ import annotations

import ast
import pathlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.repositories.asset_relations_repository as repo_mod
import app.services.library.generated_media_service as gm_svc
from app.db.scope import SYSTEM, UnscopedQueryError, current_scope, system_request_scope
from app.models import Resources

SCOPE = 727145299382534100
URL = "/api/v1/resources/91/cover"


# ── A. the read goes through the wrapped repository method ─────────────────


def test_the_bridge_never_selects_resources_itself():
    """``generated_media_service`` must not build its own ``Resources``
    statement: the wrap lives on ``resource_media_rows`` and a second query
    site would be a second place to forget it."""
    source = pathlib.Path(gm_svc.__file__).read_text()
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "Resources" not in names, (
        "generated_media_service now names Resources directly — route the read "
        "through AssetRelationsRepository.resource_media_rows, which carries "
        "the system_request_scope wrap Resources needs in production"
    )


@pytest.mark.asyncio
async def test_the_bridge_calls_the_wrapped_repository_method(monkeypatch):
    calls: list[tuple] = []

    class _Spy:
        async def resource_in_scope(self, resource_id, scope_id):
            calls.append(("in_scope", int(resource_id), int(scope_id)))
            return True

        async def resource_media_rows(self, ids, *, system_reason):
            calls.append(("rows", [int(i) for i in ids], system_reason))
            return {}

    monkeypatch.setattr(repo_mod, "AssetRelationsRepository", lambda: _Spy())
    async with gm_svc.resource_local_path(URL, scope_id=SCOPE):
        pass

    assert calls == [
        ("in_scope", 91, SCOPE),
        ("rows", [91], gm_svc.CANVAS_REFERENCE_READ_REASON),
    ]


# ── B/C. the wrap, through the real repository ─────────────────────────────


class _FakeSession:
    """Captures the ambient scope at the moment each statement executes."""

    def __init__(self, seen: list):
        self._seen = seen

    async def execute(self, stmt):
        self._seen.append(current_scope())

        class _R:
            @staticmethod
            def first():
                return (91,)  # resource_in_scope: yes

            @staticmethod
            def mappings():
                class _M:
                    @staticmethod
                    def all():
                        return []

                return _M()

        return _R()


def _patch_read_scope(monkeypatch, seen: list):
    @asynccontextmanager
    async def _fake_read_scope():
        yield _FakeSession(seen)

    monkeypatch.setattr(repo_mod, "read_scope", _fake_read_scope)


@pytest.mark.asyncio
async def test_the_resources_read_runs_under_system_scope_when_enforced(monkeypatch):
    seen: list = []
    _patch_read_scope(monkeypatch, seen)
    monkeypatch.setattr(repo_mod, "is_enforced", lambda table: True)

    async with gm_svc.resource_local_path(URL, scope_id=SCOPE):
        pass

    # Two statements: the scope test (ResourceItems, unscoped by design — the
    # question would be asked with its own answer suppressed from inside the
    # wrap) then the Resources read (SYSTEM).
    assert seen == [None, SYSTEM], (
        f"scopes during the bridge read were {seen!r}; expected the scope test "
        "outside the wrap and the Resources read inside it"
    )
    assert current_scope() is None, "ambient scope leaked out of the bridge"


@pytest.mark.asyncio
async def test_no_scope_is_opened_when_enforcement_is_off(monkeypatch):
    seen: list = []
    _patch_read_scope(monkeypatch, seen)
    monkeypatch.setattr(repo_mod, "is_enforced", lambda table: False)

    async with gm_svc.resource_local_path(URL, scope_id=SCOPE):
        pass

    assert seen == [None, None]


# ── D. the real choke point: negative and positive control ─────────────────


@pytest.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_unscoped_resources_select_really_raises(sqlite_session, monkeypatch):
    """NEGATIVE control — without it the SYSTEM assertion above would still
    pass if the choke point had stopped enforcing anything."""
    monkeypatch.setattr("app.db.scope._is_enforced", lambda table: True)
    with pytest.raises(UnscopedQueryError):
        await sqlite_session.execute(select(Resources.id).where(Resources.id == 91))


@pytest.mark.asyncio
async def test_system_scope_gets_the_same_select_past_the_choke_point(
    sqlite_session, monkeypatch
):
    """POSITIVE control — under SYSTEM the statement clears the guard and
    fails only on the (deliberately absent) SQLite table."""
    monkeypatch.setattr("app.db.scope._is_enforced", lambda table: True)
    async with system_request_scope(reason=gm_svc.CANVAS_REFERENCE_READ_REASON):
        with pytest.raises(OperationalError) as exc:
            await sqlite_session.execute(select(Resources.id).where(Resources.id == 91))
    assert "no such table" in str(exc.value)


@pytest.mark.asyncio
async def test_the_scope_test_reads_an_unscoped_table(sqlite_session, monkeypatch):
    """CONTROL for the control: ``resource_in_scope`` reads ``ResourceItems``,
    which carries no scope mixin — so running it OUTSIDE the wrap is correct,
    not an oversight the guard would have caught."""
    from app.models import ResourceItems

    monkeypatch.setattr("app.db.scope._is_enforced", lambda table: True)
    with pytest.raises(OperationalError) as exc:
        await sqlite_session.execute(
            select(ResourceItems.resource_id).where(ResourceItems.resource_id == 91)
        )
    assert "no such table" in str(exc.value), "expected the DB to be what stops it"
