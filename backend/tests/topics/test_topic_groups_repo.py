"""TopicGroupRepository — session boundary (converged on the ORM scopes).

The vector-bearing SQL bodies are kept (documented exceptions); these tests
stub only the read_scope/write_scope session and assert the same shapes the
old engine-boundary tests asserted.
"""

from contextlib import asynccontextmanager

import pytest

from app.repositories.topic_groups_repository import TopicGroupRepository, _to_int


def test_to_int_coerces_snowflake_str():
    assert _to_int("123") == 123
    assert _to_int(123) == 123


def test_sqlalchemy_binds_vec_with_cast_not_double_colon():
    """Root cause of the clustering 'syntax error at or near :' bug: SQLAlchemy
    text() does NOT register a bind param when it's immediately followed by the
    :: cast operator, so :vec::vector reached Postgres unbound. CAST(:vec AS
    vector) binds correctly. This guards the regression without a live DB."""
    from sqlalchemy import text

    assert "vec" in text("SELECT CAST(:vec AS vector)")._bindparams
    assert "vec" not in text("SELECT :vec::vector")._bindparams


class _Result:
    def __init__(self, *, row=None, scalar=None):
        self._row = row
        self._scalar = scalar

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar(self):
        return self._scalar


class _Session:
    def __init__(self, result=None):
        self.calls: list = []
        self._result = result if result is not None else _Result()

    async def execute(self, stmt, params=None):
        self.calls.append({"stmt": stmt, "params": params})
        return self._result


def _patch(monkeypatch, session):
    from app.repositories import topic_groups_repository as mod

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)
    monkeypatch.setattr(mod, "write_scope", _scope)
    return session


@pytest.mark.asyncio
async def test_nearest_group_passes_vec_and_window(monkeypatch):
    session = _patch(monkeypatch, _Session(_Result(row={"id": "9", "sim": 0.9})))

    out = await TopicGroupRepository().nearest_group("[0.1,0.2]", window_hours=48)

    assert out == {"id": "9", "sim": 0.9}
    call = session.calls[0]
    assert call["params"] == {"vec": "[0.1,0.2]", "win": 48}
    sql = str(call["stmt"])
    # CAST(:vec AS vector), NOT :vec::vector — SQLAlchemy text() leaves a bind
    # param unbound when it's immediately followed by the :: cast operator.
    assert "<=>" in sql and "CAST(:vec AS vector)" in sql
    assert ":vec::vector" not in sql


@pytest.mark.asyncio
async def test_assign_hotspot_binds_ints(monkeypatch):
    session = _patch(monkeypatch, _Session())

    # snowflake ids arrive as strings; must be bound as ints (asyncpg int8 strict)
    await TopicGroupRepository().assign_hotspot("100", "200")

    stmt = session.calls[0]["stmt"]
    sql = str(stmt).lower()
    assert "update public.hotspots" in sql and "topic_group_id" in sql
    params = stmt.compile().params
    assert 100 in params.values() and 200 in params.values()
    assert all(isinstance(v, int) for v in params.values())


@pytest.mark.asyncio
async def test_create_group_returns_id(monkeypatch):
    session = _patch(monkeypatch, _Session(_Result(scalar=555)))

    gid = await TopicGroupRepository().create_group(label="T", vec="[1,2]")

    assert gid == "555"
    call = session.calls[0]
    assert call["params"]["label"] == "T"
    assert call["params"]["vec"] == "[1,2]"
    assert "RETURNING id::text" in str(call["stmt"])


@pytest.mark.asyncio
async def test_recompute_group_updates_source_count(monkeypatch):
    session = _patch(monkeypatch, _Session())

    await TopicGroupRepository().recompute_group("42")

    call = session.calls[0]
    assert call["params"] == {"g": 42}
    sql = str(call["stmt"])
    assert "count(DISTINCT source_id)" in sql
    # also persists the distinct member source labels (for the "which
    # platforms" feed tooltip) in the same aggregate pass.
    assert "source_labels" in sql
    assert "array_agg(DISTINCT source_label" in sql
