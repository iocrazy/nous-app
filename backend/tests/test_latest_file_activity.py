import pytest

from app.repositories.project_stages_repository import get_project_stages_repository


@pytest.mark.asyncio
async def test_empty_input_returns_empty():
    assert (
        await get_project_stages_repository().latest_file_activity_for_projects([])
        == {}
    )


@pytest.mark.asyncio
async def test_maps_rows_by_project(monkeypatch):
    repo = get_project_stages_repository()

    async def fake_fetch_all(sql, params):
        class _TS:
            def isoformat(self):
                return "2026-07-08T00:00:00"

        return [{"project_id": 5, "actor": "hg", "created_at": _TS()}]

    import app.db.engine as db_engine

    monkeypatch.setattr(db_engine, "fetch_all", fake_fetch_all)

    out = await repo.latest_file_activity_for_projects([5])
    assert out["5"]["kind"] == "file"
    assert out["5"]["actor"] == "hg"
    assert out["5"]["created_at"] == "2026-07-08T00:00:00"


@pytest.mark.asyncio
async def test_never_raises(monkeypatch):
    repo = get_project_stages_repository()

    async def boom(*a, **k):
        raise RuntimeError("db")

    import app.db.engine as db_engine

    monkeypatch.setattr(db_engine, "fetch_all", boom)

    assert await repo.latest_file_activity_for_projects([5]) == {}
