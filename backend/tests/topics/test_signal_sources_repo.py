"""SignalSourcesRepository — compute_health (pure) + ORM boundary.

Boundary-stub style for the DB methods: only the read_scope/write_scope
session is faked; the SQLAlchemy statement (table, filters, ordering, the
parametrized owner OR, RETURNING, bind coercion) is built for real, so a
cross-transport regression fails here rather than in prod.
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager

import pytest

from app.repositories.signal_sources_repository import (
    SignalSourcesRepository,
    compute_health,
)

# ── compute_health — pure state machine (unchanged) ─────────────────────────


def test_compute_health_success_resets():
    h = compute_health(prev_failures=2, ok=True, dead_threshold=3)
    assert h == {"health": "ok", "consecutive_failures": 0, "flipped_to_dead": False}


def test_compute_health_degraded():
    h = compute_health(prev_failures=0, ok=False, dead_threshold=3)
    assert h["health"] == "degraded" and h["consecutive_failures"] == 1
    assert h["flipped_to_dead"] is False


def test_compute_health_flips_to_dead_once():
    h = compute_health(prev_failures=2, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["consecutive_failures"] == 3
    assert h["flipped_to_dead"] is True


def test_compute_health_stays_dead_no_reflip():
    h = compute_health(prev_failures=3, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["flipped_to_dead"] is False


# ── ORM boundary ────────────────────────────────────────────────────────────


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, result=None):
        self.statements = []
        self._result = result

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return self._result


def _cm(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


class _Src:
    """Minimal ORM row stand-in exposing __table__ columns."""

    def __init__(self, **vals):
        self._vals = vals
        from app.models import SignalSources

        self.__table__ = SignalSources.__table__

    def __getattr__(self, name):
        # Unset columns read back as None (mirrors a row with NULLs), so
        # _row_dict can iterate every mapped column.
        return self._vals.get(name)


@pytest.mark.asyncio
async def test_tier_map_defaults_null_to_2(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    session = _FakeSession(_RowsResult([(1, 3), (2, None)]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    out = await SignalSourcesRepository().tier_map()

    assert out == {"1": 3, "2": 2}


@pytest.mark.asyncio
async def test_list_all_orders_worst_first(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    session = _FakeSession(_ScalarResult([_Src(id=1, name="A", health="dead")]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    out = await SignalSourcesRepository().list_all()

    assert out[0]["id"] == 1 and out[0]["health"] == "dead"
    sql = str(session.statements[0])
    assert "FROM public.signal_sources" in sql
    assert "ORDER BY" in sql and "health" in sql and "name" in sql


@pytest.mark.asyncio
async def test_list_visible_is_parametrized_owner_or(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    session = _FakeSession(_ScalarResult([]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    await SignalSourcesRepository().list_visible("u1")

    stmt = session.statements[0]
    sql = str(stmt)
    # system (NULL) OR own — parametrized, no interpolated filter string.
    assert "user_id IS NULL" in sql
    assert "user_id =" in sql
    assert "u1" in stmt.compile().params.values()


@pytest.mark.asyncio
async def test_create_source_returns_inserted_row(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    row = {"id": 5, "user_id": "u1", "kind": "rss", "name": "n", "tier": 2}
    session = _FakeSession(_MappingResult([row]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    out = await SignalSourcesRepository().create_source(
        user_id="u1", kind="rss", name="n", config={}, category=None
    )

    assert out["id"] == 5 and out["kind"] == "rss"
    sql = str(session.statements[0]).lower()
    assert "insert into public.signal_sources" in sql and "returning" in sql


@pytest.mark.asyncio
async def test_admin_update_no_change_reads_existing(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    # No enabled/tier passed → falls back to get_source (a read).
    session = _FakeSession(_ScalarResult([_Src(id=7, name="x")]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    def _no_write():  # pragma: no cover
        raise AssertionError("write_scope must not open when nothing changed")

    monkeypatch.setattr(mod, "write_scope", _no_write)

    out = await SignalSourcesRepository().admin_update("7")
    assert out["id"] == 7


@pytest.mark.asyncio
async def test_admin_update_patches_and_coerces(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    session = _FakeSession(_MappingResult([{"id": 7, "tier": 3, "enabled": False}]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    await SignalSourcesRepository().admin_update("7", enabled=False, tier=3)

    stmt = session.statements[0]
    sql = str(stmt).lower()
    assert "update public.signal_sources" in sql and "returning" in sql
    assert 7 in stmt.compile().params.values()  # id str→int


@pytest.mark.asyncio
async def test_delete_source_scopes_by_owner(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    session = _FakeSession(_RowsResult([(5,)]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    ok = await SignalSourcesRepository().delete_source(user_id="u1", source_id="5")

    assert ok is True
    stmt = session.statements[0]
    sql = str(stmt)
    assert "DELETE FROM public.signal_sources" in sql
    assert "user_id" in sql and "RETURNING" in sql


@pytest.mark.asyncio
async def test_delete_source_false_when_not_owned(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    monkeypatch.setattr(mod, "write_scope", _cm(_FakeSession(_RowsResult([]))))
    ok = await SignalSourcesRepository().delete_source(user_id="u1", source_id="5")
    assert ok is False


@pytest.mark.asyncio
async def test_mark_health_reads_then_writes_datetime(monkeypatch):
    from app.repositories import signal_sources_repository as mod

    read_session = _FakeSession(_RowsResult([(2,)]))  # prev consecutive_failures
    write_session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", _cm(read_session))
    monkeypatch.setattr(mod, "write_scope", _cm(write_session))

    state = await SignalSourcesRepository().mark_health("5", ok=False)

    # 2 prev + this failure = 3 → dead (dead_threshold default 3).
    assert state["health"] == "dead" and state["consecutive_failures"] == 3
    # last_fetched_at bound as a datetime object, not an ISO string.
    params = write_session.statements[0].compile().params
    assert any(isinstance(v, datetime.datetime) for v in params.values())


def test_repo_has_no_supabase_client():
    import inspect

    from app.repositories import signal_sources_repository as mod

    src = inspect.getsource(mod)
    assert "get_async_supabase_admin" not in src
    assert "client.table" not in src
