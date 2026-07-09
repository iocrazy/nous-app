import pytest

from app.services.library.projects_service import ProjectsService


class _FakeShotsRepo:
    def __init__(self, empty_ids):
        self.empty_ids = empty_ids
        self.status_calls = []

    async def list_empty_shot_ids_for_project(self, pid):
        return list(self.empty_ids)

    async def update_status(self, shot_id, status):
        self.status_calls.append((shot_id, status))


def _svc(empty_ids):
    s = ProjectsService.__new__(ProjectsService)
    s._shots_repo_override = _FakeShotsRepo(empty_ids)
    return s


async def _noop_style(pid):
    return {}


@pytest.mark.asyncio
async def test_dispatches_one_per_empty_shot(monkeypatch):
    svc = _svc(["s1", "s2", "s3"])
    created = {}
    dispatched = []

    class _Mgr:
        async def create(self, **kw):
            created.update(kw)
            return "parent-task-1"

    async def fake_start(name, **kw):
        dispatched.append(kw["dbos_workflow_kwargs"]["shot_id"])

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager", lambda: _Mgr()
    )
    monkeypatch.setattr(
        "app.services.library.projects_service.start_workflow_routed", fake_start
    )
    monkeypatch.setattr(
        "app.services.library.projects_service._load_style_profile",
        _noop_style,
        raising=False,
    )

    out = await svc.generate_missing_frames("proj1", "user1")
    assert out["dispatched_count"] == 3
    assert out["parent_task_id"] == "parent-task-1"
    assert sorted(dispatched) == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_no_empty_shots_dispatches_zero(monkeypatch):
    svc = _svc([])

    class _Mgr:
        async def create(self, **kw):
            return "parent-task-2"

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager", lambda: _Mgr()
    )
    out = await svc.generate_missing_frames("proj1", "user1")
    assert out["dispatched_count"] == 0
