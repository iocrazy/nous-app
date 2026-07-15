"""UserTopicInterestRepository — session boundary (ORM scopes).

get_interest is a real select(Model); set_interest / rank_hotspot_ids keep
their SQL bodies (vector CAST + CTE keyword rank) on the session scopes.
"""

from contextlib import asynccontextmanager

import pytest

from app.repositories.user_topic_interest_repository import (
    UserTopicInterestRepository,
)


class _Result:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self._row

    def all(self):
        return self._rows


class _Session:
    def __init__(self, result=None):
        self.calls: list = []
        self._result = result if result is not None else _Result()

    async def execute(self, stmt, params=None):
        self.calls.append({"stmt": stmt, "params": params})
        return self._result


def _patch(monkeypatch, session):
    from app.repositories import user_topic_interest_repository as mod

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)
    monkeypatch.setattr(mod, "write_scope", _scope)
    return session


@pytest.mark.asyncio
async def test_get_interest_passes_user_id(monkeypatch):
    session = _patch(
        monkeypatch,
        _Session(
            _Result(
                row={"interest_text": "ai", "has_embedding": True, "updated_at": "t"}
            )
        ),
    )

    out = await UserTopicInterestRepository().get_interest("u1")

    assert out["interest_text"] == "ai" and out["has_embedding"] is True
    stmt = session.calls[0]["stmt"]
    assert "u1" in stmt.compile().params.values()
    sql = str(stmt)
    assert "FROM public.user_topic_interests" in sql
    assert "has_embedding" in sql  # embedding IS NOT NULL projection


@pytest.mark.asyncio
async def test_set_interest_binds_text_and_vec(monkeypatch):
    session = _patch(monkeypatch, _Session())

    await UserTopicInterestRepository().set_interest(
        "u1", interest_text="ai chips", vec="[0.1,0.2]"
    )

    call = session.calls[0]
    assert call["params"] == {"uid": "u1", "txt": "ai chips", "vec": "[0.1,0.2]"}
    sql = str(call["stmt"])
    # CAST(:vec AS vector), never :vec::vector (SQLAlchemy bind gotcha)
    assert "CAST(:vec AS vector)" in sql
    assert ":vec::vector" not in sql


@pytest.mark.asyncio
async def test_set_interest_null_vec(monkeypatch):
    session = _patch(monkeypatch, _Session())

    # provider unconfigured -> vec None -> embedding column set NULL
    await UserTopicInterestRepository().set_interest("u1", interest_text="x", vec=None)

    assert session.calls[0]["params"]["vec"] is None


@pytest.mark.asyncio
async def test_rank_hotspot_ids_returns_ordered_ids(monkeypatch):
    session = _patch(
        monkeypatch,
        _Session(_Result(rows=[{"id": "7"}, {"id": "3"}, {"id": "9"}])),
    )

    ids = await UserTopicInterestRepository().rank_hotspot_ids("u1", limit=50)

    assert ids == ["7", "3", "9"]
    call = session.calls[0]
    assert call["params"]["uid"] == "u1" and call["params"]["lim"] == 50
    sql = str(call["stmt"])
    # embedding semantic ranking is still present...
    assert "<=>" in sql
    # ...AND the interest text now drives a keyword filter (split → match
    # title/body), so For You works even without an embedding.
    assert "regexp_split_to_array" in sql
    assert "unnest(me.words)" in sql
    assert "h.content_original" in sql
