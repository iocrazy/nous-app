import pytest

from app.repositories.script_shot_repository import get_script_shot_repository


@pytest.mark.asyncio
async def test_progress_zero_when_query_fails(monkeypatch):
    """Best-effort: any DB error → all-zero dict, never raises."""
    repo = get_script_shot_repository()

    async def boom(*a, **k):
        raise RuntimeError("db down")

    import app.db.engine as db_engine

    monkeypatch.setattr(db_engine, "fetch_one", boom)

    out = await repo.storyboard_progress_for_project(123)
    assert out == {
        "total": 0,
        "done": 0,
        "empty": 0,
        "generating": 0,
        "failed": 0,
        "script_count": 0,
        "scene_count": 0,
    }


@pytest.mark.asyncio
async def test_progress_shape_from_row(monkeypatch):
    """Aggregated row → typed int dict."""
    repo = get_script_shot_repository()

    async def fake_fetch_one(sql, params):
        assert params == {"pid": 123}
        return {
            "total": 12,
            "done": 9,
            "empty": 3,
            "generating": 0,
            "failed": 0,
            "script_count": 2,
            "scene_count": 5,
        }

    import app.db.engine as db_engine

    monkeypatch.setattr(db_engine, "fetch_one", fake_fetch_one)

    out = await repo.storyboard_progress_for_project(123)
    assert out["total"] == 12 and out["done"] == 9 and out["empty"] == 3
    assert out["script_count"] == 2 and out["scene_count"] == 5
