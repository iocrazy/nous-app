"""Unit tests for the episode progress aggregate (PR-10a, spec G12).

GET /projects/{project_id}/episodes/progress — per-episode script/scene/
shot counts + a derived pipeline status. Covers the pure status-derivation
matrix and the repository row-shaping (monkeypatched db_engine.fetch_all,
no live DB).
"""

from __future__ import annotations

import pytest

from app.repositories.episode_repository import (
    EpisodeRepository,
    _derive_episode_status,
    _progress_row,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# _derive_episode_status — pure function, full state matrix
# --------------------------------------------------------------------------- #


def test_status_planned_when_no_scripts():
    assert _derive_episode_status(0, 0, 0, 0, 0) == "planned"


def test_status_drafting_when_script_but_no_shots():
    assert _derive_episode_status(1, 3, 0, 0, 0) == "drafting"


def test_status_drafting_when_script_but_no_scenes():
    # Not explicitly enumerated by spec, but a script with zero scenes must
    # not read as "planned" (a script does exist) nor as boarding/boarded
    # (no shots exist yet).
    assert _derive_episode_status(1, 0, 0, 0, 0) == "drafting"


def test_status_boarding_when_shots_started_not_all_done():
    assert _derive_episode_status(1, 2, 5, 2, 0) == "boarding"


def test_status_boarded_when_all_shots_done():
    assert _derive_episode_status(1, 2, 5, 5, 0) == "boarded"


def test_status_rendered_wins_over_boarded():
    assert _derive_episode_status(1, 2, 5, 5, 3) == "rendered"


def test_status_rendered_wins_over_boarding():
    # A partially-shot episode that already has a render present (e.g. a
    # re-render of one shot) still reads as rendered — highest wins.
    assert _derive_episode_status(1, 2, 5, 1, 1) == "rendered"


# --------------------------------------------------------------------------- #
# _progress_row — shape + stringified bigint id
# --------------------------------------------------------------------------- #


def test_progress_row_shapes_and_stringifies_episode_id():
    row = {
        "episode_id": 318252341326512,  # > 2^53, JS precision trap
        "title": "Ep 1",
        "sort_order": 0,
        "script_count": 1,
        "scene_count": 3,
        "shots_total": 5,
        "shots_done": 5,
        "renders_count": 2,
    }
    out = _progress_row(row)
    assert out["episode_id"] == "318252341326512"
    assert isinstance(out["episode_id"], str)
    assert out["title"] == "Ep 1"
    assert out["sort_order"] == 0
    assert out["script_count"] == 1
    assert out["scene_count"] == 3
    assert out["shots_total"] == 5
    assert out["shots_done"] == 5
    assert out["renders_count"] == 2
    assert out["status"] == "rendered"


def test_progress_row_coerces_none_counts_to_zero():
    row = {
        "episode_id": 1,
        "title": "Ep 2",
        "sort_order": 1,
        "script_count": None,
        "scene_count": None,
        "shots_total": None,
        "shots_done": None,
        "renders_count": None,
    }
    out = _progress_row(row)
    assert out["script_count"] == 0
    assert out["status"] == "planned"


# --------------------------------------------------------------------------- #
# EpisodeRepository.progress_by_project — monkeypatched fetch_all
# --------------------------------------------------------------------------- #


def test_progress_sql_uses_single_or_filter_for_renders_count():
    """Regression guard for the double-count bug: renders_count must be one
    FILTER with an OR over (image_url, video_url), not two independent
    FILTERed counts summed together — a shot with BOTH urls (the
    image-then-video generation flow) would otherwise be counted twice."""
    import app.repositories.episode_repository as mod

    sql = mod._PROGRESS_SQL
    assert (
        "WHERE sh.image_url IS NOT NULL OR sh.video_url IS NOT NULL" in sql
    ), "renders_count must use a single OR-combined FILTER, not two summed FILTERs"
    # Guard against a regression back to the old "COUNT(...) + COUNT(...)"
    # double-FILTER shape landing again.
    renders_count_clause = sql.split("AS renders_count")[0].rsplit(
        "COUNT(DISTINCT sh.id) FILTER", 1
    )[-1]
    assert "+" not in renders_count_clause


@pytest.mark.asyncio
async def test_progress_by_project_maps_rows_and_passes_project_id(monkeypatch):
    import app.repositories.episode_repository as mod

    captured: dict = {}

    async def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "episode_id": 111,
                "title": "Ep 1",
                "sort_order": 0,
                "script_count": 1,
                "scene_count": 2,
                "shots_total": 4,
                "shots_done": 4,
                "renders_count": 1,
            },
            {
                "episode_id": 222,
                "title": "Ep 2",
                "sort_order": 1,
                "script_count": 0,
                "scene_count": 0,
                "shots_total": 0,
                "shots_done": 0,
                "renders_count": 0,
            },
            {
                # A shot with BOTH image_url and video_url (image-then-video
                # flow) must be reflected by the mocked DB as renders_count=1
                # here — this fixture documents the row shape the real
                # OR-FILTER SQL is expected to produce; the actual SQL
                # aggregation is proven for real in
                # tests/integration/test_episodes_progress_db.py.
                "episode_id": 333,
                "title": "Ep 3",
                "sort_order": 2,
                "script_count": 1,
                "scene_count": 1,
                "shots_total": 1,
                "shots_done": 1,
                "renders_count": 1,
            },
        ]

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    items = await EpisodeRepository().progress_by_project("999")

    assert captured["params"] == {"project_id": 999}
    assert len(items) == 3
    assert items[0]["episode_id"] == "111"
    assert items[0]["status"] == "rendered"
    assert items[1]["episode_id"] == "222"
    assert items[1]["status"] == "planned"
    assert items[2]["episode_id"] == "333"
    assert items[2]["renders_count"] == 1
    assert items[2]["status"] == "rendered"


@pytest.mark.asyncio
async def test_progress_by_project_empty_when_no_rows(monkeypatch):
    import app.repositories.episode_repository as mod

    async def fake_fetch_all(sql, params):
        return []

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    items = await EpisodeRepository().progress_by_project("999")
    assert items == []


@pytest.mark.asyncio
async def test_progress_by_project_propagates_failure(monkeypatch):
    """A primary read — a query failure is NOT swallowed into []; it
    propagates so the router's generic except->500 fires (matches the
    task's 'failures may 500 normally like siblings' contract)."""
    import app.repositories.episode_repository as mod

    async def fake_fetch_all(sql, params):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    with pytest.raises(RuntimeError):
        await EpisodeRepository().progress_by_project("999")
