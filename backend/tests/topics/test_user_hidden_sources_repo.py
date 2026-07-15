"""UserHiddenSourcesRepository — ORM boundary (migrated off supabase-py).

Boundary-stub style: only the read_scope/write_scope session is faked; the
SQLAlchemy statement (table, filters, bind coercion) is built for real, so a
cross-transport regression (wrong table, missing user scope, str-vs-int id
bind) fails here rather than in prod.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


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


@pytest.mark.asyncio
async def test_list_hidden_ids_selects_source_id_by_user(monkeypatch):
    from app.repositories import user_hidden_sources_repository as mod
    from app.repositories.user_hidden_sources_repository import (
        UserHiddenSourcesRepository,
    )

    session = _FakeSession(_ScalarsResult([101, 202]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    out = await UserHiddenSourcesRepository().list_hidden_ids("u1")

    assert out == ["101", "202"]  # stringified
    sql = str(session.statements[0])
    assert "FROM public.user_hidden_sources" in sql
    assert "source_id" in sql and "user_id" in sql


@pytest.mark.asyncio
async def test_hide_is_insert_on_conflict_do_nothing_with_bigint(monkeypatch):
    from app.repositories import user_hidden_sources_repository as mod
    from app.repositories.user_hidden_sources_repository import (
        UserHiddenSourcesRepository,
    )

    session = _FakeSession()
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    await UserHiddenSourcesRepository().hide("u1", "999")

    stmt = session.statements[0]
    sql = str(stmt).lower()
    assert "insert into public.user_hidden_sources" in sql
    assert "on conflict" in sql and "do nothing" in sql
    # str source_id coerced to int for the bigint column.
    assert 999 in stmt.compile().params.values()


@pytest.mark.asyncio
async def test_unhide_is_compound_delete_with_bigint(monkeypatch):
    from app.repositories import user_hidden_sources_repository as mod
    from app.repositories.user_hidden_sources_repository import (
        UserHiddenSourcesRepository,
    )

    session = _FakeSession()
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    await UserHiddenSourcesRepository().unhide("u1", "999")

    stmt = session.statements[0]
    sql = str(stmt)
    assert "DELETE FROM public.user_hidden_sources" in sql
    assert "user_id" in sql and "source_id" in sql
    assert 999 in stmt.compile().params.values()


@pytest.mark.asyncio
async def test_hide_reraises_on_error(monkeypatch):
    from app.repositories import user_hidden_sources_repository as mod
    from app.repositories.user_hidden_sources_repository import (
        UserHiddenSourcesRepository,
    )

    class _Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr(mod, "write_scope", _cm(_Boom()))
    with pytest.raises(RuntimeError):
        await UserHiddenSourcesRepository().hide("u1", "1")


def test_leaf_repos_have_no_supabase_client():
    import inspect

    from app.repositories import (
        hotspot_user_state_repository,
        user_hidden_sources_repository,
    )

    for mod in (user_hidden_sources_repository, hotspot_user_state_repository):
        src = inspect.getsource(mod)
        assert "get_async_supabase_admin" not in src, mod.__name__
        assert "client.table" not in src, mod.__name__
