"""ORM warm-up migration: the last supabase-py `.table()` stragglers.

Covers the two mixed-repo leftovers migrated to the ORM session scopes:

- ``UserSettingsRepository.delete`` — was ``client.table("user_settings")
  .delete().eq("user_id", ...)``; now a Core DELETE inside the committing
  ``write_scope()`` session.
- ``MediaRepository.get_media_owner_map`` / ``get_media_resource_owner_map``
  — were ``client.table("resources").select(...)`` REST reads; now
  ``select(Resources.media_id, ...)`` on ``read_scope()`` with ``_bigint``
  coercion (asyncpg int8 codec is strict — PostgREST used to coerce the
  stringified ids server-side).

Style: stub ONLY the session boundary (fake session capturing the real
SQLAlchemy statement) — the statement construction itself runs for real, so
cross-transport drift (wrong table, missing filter, str-vs-int binds) fails
here instead of in prod (the canvas data-source audit lesson).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

# ── Shared fakes ───────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Captures every executed statement; returns canned rows."""

    def __init__(self, rows=None, raise_on_execute=False):
        self.statements = []
        self._rows = rows or []
        self._raise = raise_on_execute

    async def execute(self, stmt, params=None):
        if self._raise:
            raise RuntimeError("boom")
        self.statements.append(stmt)
        return _FakeResult(self._rows)


def _scope_cm(session):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


# ── UserSettingsRepository.delete ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_settings_delete_runs_core_delete_in_write_scope(monkeypatch):
    """delete() issues ``DELETE FROM user_settings WHERE user_id = :uid`` via
    the committing write_scope() session and invalidates the cache."""
    from app.db import session as db_session
    from app.repositories import user_settings_repository as mod
    from app.repositories.user_settings_repository import UserSettingsRepository

    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", _scope_cm(session))

    invalidated = []
    monkeypatch.setattr(
        mod.user_settings_cache, "invalidate", lambda uid: invalidated.append(uid)
    )

    ok = await UserSettingsRepository().delete("user-1")

    assert ok is True
    assert len(session.statements) == 1
    sql = str(session.statements[0])
    assert sql.startswith("DELETE FROM") and "user_settings" in sql
    assert "user_id" in sql
    params = session.statements[0].compile().params
    assert list(params.values()) == ["user-1"]
    assert invalidated == ["user-1"]


@pytest.mark.asyncio
async def test_user_settings_delete_returns_false_on_error(monkeypatch):
    """A raising session → False (legacy contract: never raises)."""
    from app.db import session as db_session
    from app.repositories.user_settings_repository import UserSettingsRepository

    monkeypatch.setattr(
        db_session, "write_scope", _scope_cm(_FakeSession(raise_on_execute=True))
    )

    assert await UserSettingsRepository().delete("user-1") is False


def test_user_settings_repo_has_no_supabase_client():
    """The supabase-py surface is fully retired from this repo."""
    import inspect

    from app.repositories import user_settings_repository as mod

    src = inspect.getsource(mod)
    assert "get_async_supabase_admin" not in src
    assert "client.table" not in src


# ── MediaRepository owner maps ─────────────────────────────────────────────


def _mk_media_repo():
    from app.repositories.media_repository import MediaRepository

    return MediaRepository()


@pytest.mark.asyncio
async def test_get_media_owner_map_selects_resources_with_bigint_ids(monkeypatch):
    """The read targets resources(media_id, creator_id), coerces str ids to
    int for the IN bind, filters is_trashed=false, and returns
    {media_id_str: creator_id_str} with first-wins dedup."""
    from app.repositories import media_repository as mod

    cid_a = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    cid_b = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    rows = [
        (101, cid_a),
        (101, cid_b),  # duplicate media_id — first wins
        (202, None),  # falsy creator — skipped
        (303, cid_b),
    ]
    session = _FakeSession(rows=rows)
    monkeypatch.setattr(mod, "read_scope", _scope_cm(session))

    out = await _mk_media_repo().get_media_owner_map(["101", "202", 303])

    assert out == {"101": str(cid_a), "303": str(cid_b)}
    assert len(session.statements) == 1
    stmt = session.statements[0]
    sql = str(stmt)
    assert "FROM public.resources" in sql
    assert "media_id" in sql and "creator_id" in sql
    assert "is_trashed" in sql
    # str ids must be bigint-coerced before binding (asyncpg int8 is strict).
    compiled = stmt.compile()
    in_bind = [v for v in compiled.params.values() if isinstance(v, list)]
    assert in_bind and in_bind[0] == [101, 202, 303]


@pytest.mark.asyncio
async def test_get_media_owner_map_empty_input_short_circuits(monkeypatch):
    from app.repositories import media_repository as mod

    def _explode():  # pragma: no cover - must not be called
        raise AssertionError("read_scope must not be opened for empty input")

    monkeypatch.setattr(mod, "read_scope", _explode)
    assert await _mk_media_repo().get_media_owner_map([]) == {}


@pytest.mark.asyncio
async def test_get_media_owner_map_returns_empty_on_error(monkeypatch):
    from app.repositories import media_repository as mod

    monkeypatch.setattr(
        mod, "read_scope", _scope_cm(_FakeSession(raise_on_execute=True))
    )
    assert await _mk_media_repo().get_media_owner_map(["1"]) == {}


@pytest.mark.asyncio
async def test_get_media_resource_owner_map_shape(monkeypatch):
    """Returns {media_id: {"user_id": ..., "resource_id": ...}} — all str,
    same dedup/skip rules as get_media_owner_map."""
    from app.repositories import media_repository as mod

    cid = uuid.UUID("00000000-0000-0000-0000-0000000000cc")
    rows = [
        (9001, 101, cid),  # (resource id, media_id, creator_id)
        (9002, 101, cid),  # dup media — first wins
        (9003, 202, None),  # falsy creator — skipped
    ]
    session = _FakeSession(rows=rows)
    monkeypatch.setattr(mod, "read_scope", _scope_cm(session))

    out = await _mk_media_repo().get_media_resource_owner_map([101, 202])

    assert out == {"101": {"user_id": str(cid), "resource_id": "9001"}}
    sql = str(session.statements[0])
    assert "FROM public.resources" in sql and "is_trashed" in sql


@pytest.mark.asyncio
async def test_owner_maps_wrap_system_scope_only_when_enforced(monkeypatch):
    """resources carries UserScoped: when enforcement is ON the cross-user
    owner-map read must open system_request_scope; flag-off it must NOT
    (byte-for-byte legacy path, no spurious audit rows)."""
    from app.repositories import media_repository as mod

    opened = []

    @asynccontextmanager
    async def _fake_sys_scope(reason):
        opened.append(reason)
        yield

    session = _FakeSession(rows=[])
    monkeypatch.setattr(mod, "read_scope", _scope_cm(session))
    monkeypatch.setattr(mod, "system_request_scope", _fake_sys_scope)

    monkeypatch.setattr(mod, "is_enforced", lambda t: False)
    await _mk_media_repo().get_media_owner_map(["1"])
    assert opened == []

    monkeypatch.setattr(mod, "is_enforced", lambda t: True)
    await _mk_media_repo().get_media_owner_map(["1"])
    assert opened and "owner-map" in opened[0]


def test_media_repo_has_no_supabase_client():
    """The supabase-py surface is fully retired from this repo."""
    import inspect

    from app.repositories import media_repository as mod

    src = inspect.getsource(mod)
    assert "get_async_supabase_admin" not in src
    assert "client.table" not in src
