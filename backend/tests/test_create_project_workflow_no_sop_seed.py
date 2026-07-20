"""W2-1: create_project + resolve_current_stage stand down the SOP path for
workflow projects.

- Born WITH a workflow template → the SOP first-stage seed is skipped (the node
  chain, instantiated afterwards, is the stage source); the workflow
  instantiation still runs.
- Born WITHOUT a template → the SOP first-stage seed runs, unchanged.
- resolve_current_stage on a workflow project → no forward auto-promote, no
  persistence side effects; the stored stage is returned read-only.
"""

from __future__ import annotations

import pytest

import app.repositories.project_stages_repository as stages_module
from app.services.library.projects_service import (
    ProjectsService,
    resolve_current_stage,
)


class _FakeStages:
    def __init__(self):
        self.set_calls = []

    async def list_catalog(self):
        return [{"id": "31", "slug": "planning", "name": "Planning", "sort_order": 10}]

    async def set_current_stage(self, project_id, stage_id, user_id):
        self.set_calls.append((project_id, stage_id, user_id))
        return {"id": str(stage_id), "slug": "planning", "name": "Planning"}


class _FakeRepo:
    async def create_project(self, data):
        return {"id": 9001, "name": data.get("name", "Test Project")}


# ── born-on-first-stage: two branches ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_template_skips_sop_first_stage_seed(monkeypatch):
    svc = ProjectsService.__new__(ProjectsService)
    svc.repo = _FakeRepo()
    fake = _FakeStages()
    svc._stages_repo_override = fake

    instantiated = []

    async def _spy_instantiate(project_id, template_id, *, method=None, user_id):
        instantiated.append((project_id, template_id, method, user_id))

    monkeypatch.setattr(
        "app.services.workflow.instantiation.maybe_instantiate_project_workflow",
        _spy_instantiate,
    )

    out = await svc.create_project(
        "user-1", {"name": "WF Project", "workflow_template_id": "555"}
    )

    # SOP first-stage seed suppressed…
    assert fake.set_calls == []
    assert "current_stage_id" not in out
    # …and the workflow was instantiated instead.
    assert instantiated == [("9001", "555", None, "user-1")]


@pytest.mark.asyncio
async def test_no_template_still_seeds_sop_first_stage(monkeypatch):
    svc = ProjectsService.__new__(ProjectsService)
    svc.repo = _FakeRepo()
    fake = _FakeStages()
    svc._stages_repo_override = fake

    async def _noop_instantiate(project_id, template_id, *, method=None, user_id):
        return None

    monkeypatch.setattr(
        "app.services.workflow.instantiation.maybe_instantiate_project_workflow",
        _noop_instantiate,
    )

    out = await svc.create_project("user-1", {"name": "Plain Project"})

    # Legacy born-on-first-stage untouched for a No-workflow project.
    assert fake.set_calls == [(9001, 31, "user-1")]
    assert out["current_stage_id"] == "31"


# ── resolve_current_stage: workflow project skips persistence ─────────────────


class _ResolveStagesRepo:
    def __init__(self, current):
        self._current = current
        self.set_calls = []
        self.derive_calls = 0

    async def get_current(self, pid):
        return self._current

    async def derive_activity_flags(self, pid):
        self.derive_calls += 1
        return {"has_scenes": True, "has_shots": True, "has_renders": True}

    async def list_catalog(self):
        return [
            {"id": 1, "slug": "planning", "name": "Planning", "sort_order": 10},
            {"id": 4, "slug": "generation", "name": "Generation", "sort_order": 40},
        ]

    async def set_current_stage(self, pid, stage_id, user_id):
        self.set_calls.append((pid, stage_id, user_id))
        return {"id": stage_id, "slug": "generation", "sort_order": 40}


@pytest.mark.asyncio
async def test_resolve_current_stage_workflow_project_no_side_effects(monkeypatch):
    repo = _ResolveStagesRepo(current={"id": 1, "slug": "planning", "sort_order": 10})
    monkeypatch.setattr(stages_module, "get_project_stages_repository", lambda: repo)

    async def _has_workflow(project_id):
        return True

    monkeypatch.setattr(
        "app.services.workflow.instantiation.project_has_workflow_nodes",
        _has_workflow,
    )

    out = await resolve_current_stage(1, "u1")

    # Read-only stored stage; no derivation probe, no promote, no persistence.
    assert out == {"id": 1, "slug": "planning", "sort_order": 10}
    assert repo.derive_calls == 0
    assert repo.set_calls == []
