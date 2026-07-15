"""HotspotUserStateRepository — ORM boundary (migrated off supabase-py).

Stubs only the read_scope/write_scope session; the SQLAlchemy statement is
built for real so a cross-transport regression (wrong table, missing user
scope, str-vs-int id bind, wrong upsert SET) fails here, not in prod.
"""

from contextlib import asynccontextmanager

import pytest

from app.repositories.hotspot_user_state_repository import (
    HotspotUserStateRepository,
)


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

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


def _patch(monkeypatch, *, read=None, write=None):
    from app.repositories import hotspot_user_state_repository as mod

    def _cm(session):
        @asynccontextmanager
        async def _scope():
            yield session

        return _scope

    if read is not None:
        monkeypatch.setattr(mod, "read_scope", _cm(read))
    if write is not None:
        monkeypatch.setattr(mod, "write_scope", _cm(write))


@pytest.mark.asyncio
async def test_get_states_maps_by_id(monkeypatch):
    rows = [(5, True, False, False), (9, False, True, True)]
    session = _FakeSession(_RowsResult(rows))
    _patch(monkeypatch, read=session)

    out = await HotspotUserStateRepository().get_states("u1", ["5", "9"])

    assert out["5"] == {"is_read": True, "is_saved": False, "is_hidden": False}
    assert out["9"]["is_saved"] is True and out["9"]["is_hidden"] is True
    assert "FROM public.hotspot_user_state" in str(session.statements[0])


@pytest.mark.asyncio
async def test_get_states_empty_ids_short_circuits(monkeypatch):
    from app.repositories import hotspot_user_state_repository as mod

    def _explode():  # pragma: no cover
        raise AssertionError("read_scope must not open for empty input")

    monkeypatch.setattr(mod, "read_scope", _explode)
    assert await HotspotUserStateRepository().get_states("u1", []) == {}


@pytest.mark.asyncio
async def test_list_ids_where_filters_by_flag(monkeypatch):
    session = _FakeSession(_ScalarsResult([3, 7]))
    _patch(monkeypatch, read=session)

    ids = await HotspotUserStateRepository().list_ids_where("u1", flag="is_saved")

    assert ids == ["3", "7"]
    sql = str(session.statements[0])
    assert "is_saved" in sql and "user_id" in sql


@pytest.mark.asyncio
async def test_list_ids_where_rejects_unknown_flag(monkeypatch):
    with pytest.raises(ValueError):
        await HotspotUserStateRepository().list_ids_where("u1", flag="bogus")


@pytest.mark.asyncio
async def test_set_state_upserts_only_provided_flags(monkeypatch):
    session = _FakeSession(_RowsResult([(False, True, False)]))
    _patch(monkeypatch, write=session)

    flags = await HotspotUserStateRepository().set_state("u1", "42", is_saved=True)

    stmt = session.statements[0]
    sql = str(stmt).lower()
    assert "insert into public.hotspot_user_state" in sql
    assert "on conflict" in sql and "do update" in sql
    # hotspot_id str→int coercion for the bigint column.
    assert 42 in stmt.compile().params.values()
    # RETURNING row is the source of truth for the returned flags.
    assert flags == {"is_read": False, "is_saved": True, "is_hidden": False}
