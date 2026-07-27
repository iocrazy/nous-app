"""W2-1 / M2 PR-G: create_project + resolve_current_stage and the legacy SOP
stage path.

- Born WITH a workflow template → no SOP first-stage seed (the node chain,
  instantiated afterwards, is the stage source); the workflow instantiation
  still runs.
- Born WITHOUT a template → ALSO no SOP first-stage seed anymore (M2 PR-G
  retired ``set_current_stage`` end to end — create_project no longer calls
  it for any project, workflow or not).
- resolve_current_stage on a workflow project → no forward auto-promote, no
  persistence side effects; the stored stage is returned read-only.
  (``resolve_current_stage`` itself, and its underlying repo methods, are
  intentionally out of scope for this retirement pass — see
  task-G1-report.md — so this test is unchanged.)
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
async def test_no_template_also_skips_sop_first_stage_seed(monkeypatch):
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

    # M2 PR-G: the legacy born-on-first-stage seed is retired for EVERY
    # project now, not just workflow ones — a No-workflow project has no
    # stage concept anymore (spec §8).
    assert fake.set_calls == []
    assert "current_stage_id" not in out


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
