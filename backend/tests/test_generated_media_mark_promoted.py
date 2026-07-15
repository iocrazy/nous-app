from contextlib import asynccontextmanager

import pytest


@pytest.mark.asyncio
async def test_mark_promoted_sets_and_returns(monkeypatch):
    import app.repositories.generated_media_repository as repo_mod

    captured = {}

    class _Result:
        def mappings(self):
            return self

        def first(self):
            return {"id": 7, "promoted_resource_id": 999, "scope_id": 42}

    class _Session:
        async def execute(self, stmt, params=None):
            captured["stmt"] = stmt
            return _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(repo_mod, "write_scope", _scope)

    row = await repo_mod.GeneratedMediaRepository().mark_promoted(7, 999)

    stmt = captured["stmt"]
    sql = str(stmt)
    assert "UPDATE public.generated_media" in sql
    assert "RETURNING" in sql
    params = stmt.compile().params
    assert 7 in params.values() and 999 in params.values()
    # _normalize stringifies bigint ids
    assert row["promoted_resource_id"] == "999"
    assert row["id"] == "7"
