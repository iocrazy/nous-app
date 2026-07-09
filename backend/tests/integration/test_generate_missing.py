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


class _Mgr:
    def __init__(self):
        self._counter = 0
        self.create_calls = 0
        self.fail_calls = []

    async def create(self, **kw):
        self._counter += 1
        self.create_calls += 1
        return f"t{self._counter}"

    async def fail(self, task_id, error_msg, **kw):
        self.fail_calls.append((task_id, error_msg))


@pytest.mark.asyncio
async def test_dispatches_one_per_empty_shot(monkeypatch):
    svc = _svc(["s1", "s2", "s3"])
    dispatched = []
    mgr = _Mgr()

    async def fake_start(name, **kw):
        dispatched.append(kw["dbos_workflow_kwargs"]["shot_id"])

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager", lambda: mgr
    )
    monkeypatch.setattr(
        "app.services.library.projects_service.start_workflow_routed", fake_start
    )

    out = await svc.generate_missing_frames("proj1", "user1")
    assert out["dispatched_count"] == 3
    assert len(out["task_ids"]) == 3
    assert mgr.create_calls == 3
    assert sorted(dispatched) == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_no_empty_shots_dispatches_zero(monkeypatch):
    svc = _svc([])
    mgr = _Mgr()

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager", lambda: mgr
    )

    out = await svc.generate_missing_frames("proj1", "user1")
    assert out["dispatched_count"] == 0
    assert out["task_ids"] == []
    assert mgr.create_calls == 0


@pytest.mark.asyncio
async def test_partial_failure_rolls_back_and_continues(monkeypatch):
    svc = _svc(["s1", "s2", "s3"])
    dispatched = []
    mgr = _Mgr()

    async def fake_start(name, **kw):
        shot_id = kw["dbos_workflow_kwargs"]["shot_id"]
        if shot_id == "s2":
            raise RuntimeError("boom")
        dispatched.append(shot_id)

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager", lambda: mgr
    )
    monkeypatch.setattr(
        "app.services.library.projects_service.start_workflow_routed", fake_start
    )

    out = await svc.generate_missing_frames("proj1", "user1")
    assert out["dispatched_count"] == 2
    assert sorted(dispatched) == ["s1", "s3"]

    shots_repo = svc._shots_repo_override
    assert ("s2", "empty") in shots_repo.status_calls

    assert len(mgr.fail_calls) == 1
    assert mgr.fail_calls[0][0] == "t2"
