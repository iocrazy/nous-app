"""Unit tests for the episode progress aggregate (PR-10a, spec G12).

GET /projects/{project_id}/episodes/progress — per-episode script/scene/
shot counts + a derived pipeline status. Covers the pure status-derivation
matrix and the repository row-shaping. Phase B5 Task 1: migrated to the
SQLAlchemy ORM (monkeypatched ``app.db.session.read_scope``, no live DB).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

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


def test_progress_row_carries_workflow_and_surface_state():
    row = {
        "episode_id": 42,
        "title": "Ep 1",
        "sort_order": 0,
        "current_node_id": 987654321098765,  # > 2^53, JS precision trap
        "script_count": 1,
        "scene_count": 2,
        "scene_content_count": 1,
        "shots_total": 3,
        "shots_done": 1,
        "renders_count": 0,
    }
    out = _progress_row(
        row,
        workflow_rollup={"nodes_total": 3, "nodes_done": 1, "needs_input_count": 1},
    )
    assert out["workflow"]["nodes_total"] == 3
    assert out["workflow"]["nodes_done"] == 1
    assert out["workflow"]["current_node_id"] == "987654321098765"
    assert isinstance(out["workflow"]["current_node_id"], str)
    assert out["workflow"]["needs_input_count"] == 1
    assert out["surface_state"] == {"script": True, "storyboard": False}


def test_progress_row_defaults_workflow_and_surface_state_when_absent():
    """No workflow_rollup passed (e.g. an episode with zero eligible nodes,
    absent from both rollup dicts) and no current_node_id key -> zeros/None,
    never a KeyError."""
    row = {
        "episode_id": 1,
        "title": "Ep 2",
        "sort_order": 1,
        "script_count": 0,
        "scene_count": 0,
        "shots_total": 0,
        "shots_done": 0,
        "renders_count": 0,
    }
    out = _progress_row(row)
    assert out["workflow"] == {
        "nodes_total": 0,
        "nodes_done": 0,
        "current_node_id": None,
        "needs_input_count": 0,
    }
    assert out["surface_state"] == {"script": False, "storyboard": False}


# --------------------------------------------------------------------------- #
# EpisodeRepository.progress_by_project — monkeypatched fetch_all
# --------------------------------------------------------------------------- #


def test_progress_stmt_uses_single_or_filter_for_renders_count():
    """Regression guard for the double-count bug: renders_count must be one
    FILTER with an OR over (image_url, video_url), not two independent
    FILTERed counts summed together — a shot with BOTH urls (the
    image-then-video generation flow) would otherwise be counted twice."""
    import app.repositories.episode_repository as mod

    sql = str(mod._progress_stmt(999).compile(dialect=postgresql.dialect()))
    assert (
        "FILTER (WHERE public.script_shots.image_url IS NOT NULL OR "
        "public.script_shots.video_url IS NOT NULL) AS renders_count" in sql
    ), "renders_count must use a single OR-combined FILTER, not two summed FILTERs"
    # Guard against a regression back to the old "COUNT(...) + COUNT(...)"
    # double-FILTER shape landing again.
    renders_count_clause = sql.split("AS renders_count")[0].rsplit(
        "count(distinct(public.script_shots.id)) FILTER", 1
    )[-1]
    assert "+" not in renders_count_clause


def _compile(stmt):
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


def _fake_read_scope(results_sequence=None, raise_exc=None):
    """``progress_by_project`` now issues 3 sequential execute() calls in one
    session (progress rows, nodes rollup, needs_input rollup) — the fake
    returns ``results_sequence[i]`` for the i-th call (empty list past the
    end) and records every compiled statement, not just the last one."""
    captured: dict = {"stmts": []}

    class _FakeSession:
        def __init__(self):
            self._idx = 0

        async def execute(self, stmt):
            captured["stmts"].append(stmt)
            if raise_exc is not None:
                raise raise_exc
            seq = results_sequence or []
            rows = seq[self._idx] if self._idx < len(seq) else []
            self._idx += 1
            return _FakeResult(rows)

    @asynccontextmanager
    async def _scope():
        yield _FakeSession()

    return _scope, captured


@pytest.mark.asyncio
async def test_progress_by_project_maps_rows_and_passes_project_id(monkeypatch):
    import app.repositories.episode_repository as mod

    rows = [
        {
            "episode_id": 111,
            "title": "Ep 1",
            "sort_order": 0,
            "current_node_id": None,
            "script_count": 1,
            "scene_count": 2,
            "scene_content_count": 2,
            "shots_total": 4,
            "shots_done": 4,
            "renders_count": 1,
        },
        {
            "episode_id": 222,
            "title": "Ep 2",
            "sort_order": 1,
            "current_node_id": 555,
            "script_count": 0,
            "scene_count": 0,
            "scene_content_count": 0,
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
            "current_node_id": None,
            "script_count": 1,
            "scene_count": 1,
            "scene_content_count": 1,
            "shots_total": 1,
            "shots_done": 1,
            "renders_count": 1,
        },
    ]
    nodes_rollup_rows = [
        {"episode_id": 111, "nodes_total": 3, "nodes_done": 2},
    ]
    needs_input_rollup_rows = [
        {"episode_id": 222, "needs_input_count": 1},
    ]
    fake_scope, captured = _fake_read_scope(
        [rows, nodes_rollup_rows, needs_input_rollup_rows]
    )
    monkeypatch.setattr(mod, "read_scope", fake_scope)

    items = await EpisodeRepository().progress_by_project("999")

    assert len(captured["stmts"]) == 3
    _sql, binds = _compile(captured["stmts"][0])
    assert binds["project_id_1"] == 999
    assert len(items) == 3
    assert items[0]["episode_id"] == "111"
    assert items[0]["status"] == "rendered"
    assert items[0]["workflow"] == {
        "nodes_total": 3,
        "nodes_done": 2,
        "current_node_id": None,
        "needs_input_count": 0,
    }
    assert items[0]["surface_state"] == {"script": True, "storyboard": True}
    assert items[1]["episode_id"] == "222"
    assert items[1]["status"] == "planned"
    assert items[1]["workflow"] == {
        "nodes_total": 0,
        "nodes_done": 0,
        "current_node_id": "555",
        "needs_input_count": 1,
    }
    assert items[1]["surface_state"] == {"script": False, "storyboard": False}
    assert items[2]["episode_id"] == "333"
    assert items[2]["renders_count"] == 1
    assert items[2]["status"] == "rendered"


@pytest.mark.asyncio
async def test_progress_by_project_empty_when_no_rows(monkeypatch):
    import app.repositories.episode_repository as mod

    fake_scope, _captured = _fake_read_scope([])
    monkeypatch.setattr(mod, "read_scope", fake_scope)

    items = await EpisodeRepository().progress_by_project("999")
    assert items == []


@pytest.mark.asyncio
async def test_progress_by_project_propagates_failure(monkeypatch):
    """A primary read — a query failure is NOT swallowed into []; it
    propagates so the router's generic except->500 fires (matches the
    task's 'failures may 500 normally like siblings' contract)."""
    import app.repositories.episode_repository as mod

    fake_scope, _captured = _fake_read_scope(raise_exc=RuntimeError("db down"))
    monkeypatch.setattr(mod, "read_scope", fake_scope)

    with pytest.raises(RuntimeError):
        await EpisodeRepository().progress_by_project("999")
