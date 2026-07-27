"""M2 PR-G: the legacy "born on first SOP stage" seed (set_current_stage) was
retired end-to-end — create_project no longer touches the stages repo at all,
for either a workflow or a No-workflow project (spec §8: No-workflow projects
have no stage concept)."""

import pytest

from app.services.library.projects_service import ProjectsService


class _FakeStages:
    def __init__(self):
        self.set_calls = []

    async def list_catalog(self):
        return [{"id": "31", "slug": "planning", "name": "Planning", "sort_order": 10}]


class _FakeRepo:
    async def create_project(self, data):
        return {"id": 9001, "name": data.get("name", "Test Project")}


@pytest.mark.asyncio
async def test_create_project_no_longer_seeds_a_stage():
    svc = ProjectsService.__new__(ProjectsService)
    svc.repo = _FakeRepo()
    fake = _FakeStages()
    svc._stages_repo_override = fake
    out = await svc.create_project("user-1", {"name": "Test Project"})
    # No stage seed, no current_stage_id write — the stages repo isn't
    # touched for stage-seeding purposes at all anymore.
    assert fake.set_calls == []
    assert "current_stage_id" not in out


@pytest.mark.asyncio
async def test_create_survives_stage_repo_unavailable():
    svc = ProjectsService.__new__(ProjectsService)
    svc.repo = _FakeRepo()

    class _Boom:
        async def list_catalog(self):
            raise RuntimeError("stage machine down")

    svc._stages_repo_override = _Boom()
    out = await svc.create_project("user-1", {"name": "Test Project"})
    assert out["id"] == 9001  # creation still succeeds (stages repo unused)
