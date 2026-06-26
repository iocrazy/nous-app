import pytest

import app.repositories.topic_groups_repository as mod
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


@pytest.mark.asyncio
async def test_nearest_group_passes_vec_and_window(monkeypatch):
    seen = {}

    async def _fake_fetch_one(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return {"id": "9", "sim": 0.9}

    monkeypatch.setattr(mod.db_engine, "fetch_one", _fake_fetch_one)
    out = await TopicGroupRepository().nearest_group("[0.1,0.2]", window_hours=48)
    assert out == {"id": "9", "sim": 0.9}
    assert seen["params"] == {"vec": "[0.1,0.2]", "win": 48}
    # CAST(:vec AS vector), NOT :vec::vector — SQLAlchemy text() leaves a bind
    # param unbound when it's immediately followed by the :: cast operator.
    assert "<=>" in seen["sql"] and "CAST(:vec AS vector)" in seen["sql"]
    assert ":vec::vector" not in seen["sql"]


@pytest.mark.asyncio
async def test_assign_hotspot_binds_ints(monkeypatch):
    seen = {}

    async def _fake_execute(sql, params):
        seen["params"] = params
        return 1

    monkeypatch.setattr(mod.db_engine, "execute", _fake_execute)
    # snowflake ids arrive as strings; must be bound as ints (asyncpg int8 strict)
    await TopicGroupRepository().assign_hotspot("100", "200")
    assert seen["params"] == {"g": 200, "h": 100}


@pytest.mark.asyncio
async def test_create_group_returns_id(monkeypatch):
    async def _fake_ret(sql, params):
        assert params["label"] == "T"
        assert params["vec"] == "[1,2]"
        return 555

    monkeypatch.setattr(mod.db_engine, "execute_returning_val", _fake_ret)
    gid = await TopicGroupRepository().create_group(label="T", vec="[1,2]")
    assert gid == "555"


@pytest.mark.asyncio
async def test_recompute_group_updates_source_count(monkeypatch):
    seen = {}

    async def _fake_execute(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return 1

    monkeypatch.setattr(mod.db_engine, "execute", _fake_execute)
    await TopicGroupRepository().recompute_group("42")
    assert seen["params"] == {"g": 42}
    assert "count(DISTINCT source_id)" in seen["sql"]
    # also persists the distinct member source labels (for the "which
    # platforms" feed tooltip) in the same aggregate pass.
    assert "source_labels" in seen["sql"]
    assert "array_agg(DISTINCT source_label" in seen["sql"]
