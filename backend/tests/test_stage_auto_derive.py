"""Stage auto-derivation (合一终稿): forward-only, output-driven.

The workspace stage chip is read-only — resolve_current_stage() must advance
the stored stage when real output is ahead of it, never move it backwards,
and degrade to the stored stage when derivation fails.
"""

import pytest

import app.repositories.project_stages_repository as stages_module
from app.services.library.projects_service import (
    _auto_stage_slug,
    resolve_current_stage,
)

_CATALOG = [
    {"id": 1, "slug": "planning", "name": "Planning", "sort_order": 10},
    {"id": 2, "slug": "script", "name": "Script", "sort_order": 20},
    {"id": 3, "slug": "storyboard", "name": "Storyboard", "sort_order": 30},
    {"id": 4, "slug": "generation", "name": "Generation", "sort_order": 40},
    {"id": 5, "slug": "review", "name": "Review", "sort_order": 50},
    {"id": 6, "slug": "delivery", "name": "Delivery", "sort_order": 60},
]


class _FakeStagesRepo:
    def __init__(self, current, flags, derive_error=False):
        self._current = current
        self._flags = flags
        self._derive_error = derive_error
        self.set_calls = []

    async def get_current(self, pid):
        return self._current

    async def derive_activity_flags(self, pid):
        if self._derive_error:
            raise RuntimeError("probe failed")
        return self._flags

    async def list_catalog(self):
        return _CATALOG

    async def set_current_stage(self, pid, stage_id, user_id):
        self.set_calls.append((pid, stage_id, user_id))
        stage = next(s for s in _CATALOG if s["id"] == stage_id)
        self._current = stage
        return stage


@pytest.fixture
def patch_repo(monkeypatch):
    def _install(repo):
        monkeypatch.setattr(
            stages_module, "get_project_stages_repository", lambda: repo
        )
        return repo

    return _install


# ── pure decision table ──────────────────────────────────────────────────────


def test_auto_slug_ladder():
    assert _auto_stage_slug({}) == "planning"
    assert _auto_stage_slug({"has_scenes": True}) == "script"
    assert _auto_stage_slug({"has_scenes": True, "has_shots": True}) == "storyboard"
    assert (
        _auto_stage_slug({"has_scenes": True, "has_shots": True, "has_renders": True})
        == "generation"
    )


def test_auto_slug_never_review_or_delivery():
    # Human-only stages are unreachable from output flags by construction.
    all_on = {"has_scenes": True, "has_shots": True, "has_renders": True}
    assert _auto_stage_slug(all_on) not in ("review", "delivery")


# ── resolve: forward-only promotion ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_promotes_when_output_is_ahead(patch_repo):
    repo = patch_repo(
        _FakeStagesRepo(
            current=_CATALOG[0],  # planning
            flags={"has_scenes": True, "has_shots": True, "has_renders": False},
        )
    )
    out = await resolve_current_stage(1, "u1")
    assert out["slug"] == "storyboard"
    assert repo.set_calls == [(1, 3, "u1")]


@pytest.mark.asyncio
async def test_backfills_stage_less_project(patch_repo):
    repo = patch_repo(_FakeStagesRepo(current=None, flags={}))
    out = await resolve_current_stage(1, "u1")
    assert out["slug"] == "planning"
    assert repo.set_calls == [(1, 1, "u1")]


@pytest.mark.asyncio
async def test_never_demotes_manual_review(patch_repo):
    repo = patch_repo(
        _FakeStagesRepo(
            current=_CATALOG[4],  # review (manual)
            flags={"has_scenes": True, "has_shots": True, "has_renders": True},
        )
    )
    out = await resolve_current_stage(1, "u1")
    assert out["slug"] == "review"
    assert repo.set_calls == []


@pytest.mark.asyncio
async def test_noop_when_stage_matches_output(patch_repo):
    repo = patch_repo(_FakeStagesRepo(current=_CATALOG[1], flags={"has_scenes": True}))
    out = await resolve_current_stage(1, "u1")
    assert out["slug"] == "script"
    assert repo.set_calls == []


@pytest.mark.asyncio
async def test_derive_failure_degrades_to_stored(patch_repo):
    repo = patch_repo(_FakeStagesRepo(current=_CATALOG[2], flags={}, derive_error=True))
    out = await resolve_current_stage(1, "u1")
    assert out["slug"] == "storyboard"
    assert repo.set_calls == []
