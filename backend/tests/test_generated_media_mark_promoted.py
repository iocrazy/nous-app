import pytest


@pytest.mark.asyncio
async def test_mark_promoted_sets_and_returns(monkeypatch):
    import app.repositories.generated_media_repository as repo_mod

    captured = {}

    async def _fake_exec_returning_one(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return {"id": 7, "promoted_resource_id": params["rid"], "scope_id": 42}

    monkeypatch.setattr(
        repo_mod.db_engine, "execute_returning_one", _fake_exec_returning_one
    )
    row = await repo_mod.GeneratedMediaRepository().mark_promoted(7, 999)
    assert "UPDATE public.generated_media" in captured["sql"]
    assert captured["params"] == {"id": 7, "rid": 999}
    # _normalize stringifies bigint ids
    assert row["promoted_resource_id"] == "999"
    assert row["id"] == "7"
