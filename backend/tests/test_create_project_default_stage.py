import pytest

from app.services.library.projects_service import ProjectsService


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


@pytest.mark.asyncio
async def test_create_project_defaults_to_first_stage():
    svc = ProjectsService.__new__(ProjectsService)
    svc.repo = _FakeRepo()
    fake = _FakeStages()
    svc._stages_repo_override = fake
    out = await svc.create_project("user-1", {"name": "Test Project"})
    assert fake.set_calls == [(9001, 31, "user-1")]
    assert out["current_stage_id"] == "31"


@pytest.mark.asyncio
async def test_create_survives_stage_init_failure():
    svc = ProjectsService.__new__(ProjectsService)
    svc.repo = _FakeRepo()

    class _Boom:
        async def list_catalog(self):
            raise RuntimeError("stage machine down")

    svc._stages_repo_override = _Boom()
    out = await svc.create_project("user-1", {"name": "Test Project"})
    assert out["id"] == 9001  # creation still succeeds
