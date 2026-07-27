"""W2-1 / M2 PR-G + PR-G1.5: create_project + resolve_current_stage and the
legacy SOP stage path.

- Born WITH a workflow template → no SOP first-stage seed (the node chain,
  instantiated afterwards, is the stage source); the workflow instantiation
  still runs.
- Born WITHOUT a template → ALSO no SOP first-stage seed anymore (M2 PR-G
  retired ``set_current_stage`` end to end — create_project no longer calls
  it for any project, workflow or not).
- resolve_current_stage → always ``None`` now, for every project (M2 PR-G1.5
  retired the read side too: ``get_current`` / ``derive_activity_flags`` /
  ``set_current_stage`` no longer exist on ``ProjectStagesRepository``, so
  there is nothing left to resolve, workflow project or not).
"""

from __future__ import annotations

import pytest

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


# ── resolve_current_stage: always None now (M2 PR-G1.5) ───────────────────────


@pytest.mark.asyncio
async def test_resolve_current_stage_always_returns_none():
    """The legacy SOP stage cursor is retired end-to-end — there is no more
    repo call to make, workflow project or not; the function is a thin stub
    that degrades any lingering caller to ``None`` instead of crashing."""
    assert await resolve_current_stage(1, "u1") is None
