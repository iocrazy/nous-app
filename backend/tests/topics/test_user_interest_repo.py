import pytest

import app.repositories.user_topic_interest_repository as mod
from app.repositories.user_topic_interest_repository import (
    UserTopicInterestRepository,
)


@pytest.mark.asyncio
async def test_get_interest_passes_user_id(monkeypatch):
    seen = {}

    async def _fake_fetch_one(sql, params):
        seen["params"] = params
        return {"interest_text": "ai", "has_embedding": True, "updated_at": "t"}

    monkeypatch.setattr(mod.db_engine, "fetch_one", _fake_fetch_one)
    out = await UserTopicInterestRepository().get_interest("u1")
    assert out["interest_text"] == "ai" and out["has_embedding"] is True
    assert seen["params"] == {"uid": "u1"}


@pytest.mark.asyncio
async def test_set_interest_binds_text_and_vec(monkeypatch):
    seen = {}

    async def _fake_execute(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return 1

    monkeypatch.setattr(mod.db_engine, "execute", _fake_execute)
    await UserTopicInterestRepository().set_interest(
        "u1", interest_text="ai chips", vec="[0.1,0.2]"
    )
    assert seen["params"] == {"uid": "u1", "txt": "ai chips", "vec": "[0.1,0.2]"}
    # CAST(:vec AS vector), never :vec::vector (SQLAlchemy bind gotcha)
    assert "CAST(:vec AS vector)" in seen["sql"]
    assert ":vec::vector" not in seen["sql"]


@pytest.mark.asyncio
async def test_set_interest_null_vec(monkeypatch):
    seen = {}

    async def _fake_execute(sql, params):
        seen["params"] = params
        return 1

    monkeypatch.setattr(mod.db_engine, "execute", _fake_execute)
    # provider unconfigured -> vec None -> embedding column set NULL
    await UserTopicInterestRepository().set_interest("u1", interest_text="x", vec=None)
    assert seen["params"]["vec"] is None


@pytest.mark.asyncio
async def test_rank_hotspot_ids_returns_ordered_ids(monkeypatch):
    seen = {}

    async def _fake_fetch_all(sql, params):
        seen["params"] = params
        seen["sql"] = sql
        return [{"id": "7"}, {"id": "3"}, {"id": "9"}]

    monkeypatch.setattr(mod.db_engine, "fetch_all", _fake_fetch_all)
    ids = await UserTopicInterestRepository().rank_hotspot_ids("u1", limit=50)
    assert ids == ["7", "3", "9"]
    assert seen["params"]["uid"] == "u1" and seen["params"]["lim"] == 50
    # embedding semantic ranking is still present...
    assert "<=>" in seen["sql"]
    # ...AND the interest text now drives a keyword filter (split → match
    # title/body), so For You works even without an embedding.
    assert "regexp_split_to_array" in seen["sql"]
    assert "unnest(me.words)" in seen["sql"]
    assert "h.content_original" in seen["sql"]
