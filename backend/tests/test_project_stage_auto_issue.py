"""Project SOP stage → auto todo (issue) sync.

The manual stage-advance entry point mirrors each advance into the todo list:
a new ``status='todo'`` issue for the new stage (idempotent, never assigned),
the previous stage's issue closed. The hook is best-effort — it must never block
the advance itself, and same-stage no-ops take no issue action.

These are service-layer unit tests: the stage repo, issue repo, and projects
repo are faked (mirrors the repo-override pattern in test_stage_auto_derive.py).
"""

from __future__ import annotations

import pytest

import app.services.library.project_stage_issues as mod
from app.services.library.project_stage_issues import (
    ORIGIN_KIND,
    advance_project_stage,
    build_stage_origin_id,
)

_USER = "00000000-0000-0000-0000-000000000001"


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeStagesRepo:
    def __init__(self, current, new_stage):
        self._current = current
        self._new_stage = new_stage
        self.set_calls = []

    async def get_current(self, pid):
        return self._current

    async def set_current_stage(self, pid, stage_id, user_id):
        self.set_calls.append((pid, stage_id, user_id))
        return self._new_stage


class _FakeIssueRepo:
    def __init__(self, existing=None, create_error=False):
        # {origin_id: [issue dict, ...]}
        self._existing = existing or {}
        self._create_error = create_error
        self.created = []
        self.transitions = []

    async def list_by_origin(self, origin_kind, origin_id, *, include_hidden=False):
        return list(self._existing.get(origin_id, []))

    async def atomic_create(self, payload):
        if self._create_error:
            raise RuntimeError("db down")
        self.created.append(payload)
        return {**payload, "id": 999, "identifier": "MH-999"}

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {"id": issue_id, "status": new_status}


class _FakeProjectsRepo:
    def __init__(self, project):
        self._project = project

    async def get_project_by_id(self, pid):
        return self._project


@pytest.fixture
def patch(monkeypatch):
    def _install(*, stages, issues, project):
        monkeypatch.setattr(
            "app.repositories.project_stages_repository."
            "get_project_stages_repository",
            lambda: stages,
        )
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issues,
        )
        monkeypatch.setattr(
            "app.repositories.projects_repository.get_projects_repository",
            lambda: _FakeProjectsRepo(project),
        )

    return _install


# ── origin id helper ──────────────────────────────────────────────────────────


def test_origin_id_shape():
    assert build_stage_origin_id(5001, 9002) == "project_stage:5001:9002"
    # Snowflake bigints stay string-shaped end-to-end.
    big = "9007199254740993"
    assert build_stage_origin_id(big, "123") == f"project_stage:{big}:123"


# ── same-stage no-op ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_stage_no_op_takes_no_issue_action(patch):
    stages = _FakeStagesRepo(current={"id": "10"}, new_stage=None)  # no-op
    issues = _FakeIssueRepo()
    patch(stages=stages, issues=issues, project={"name": "P", "team_id": None})

    result = await advance_project_stage(100, 10, _USER)

    assert result is None
    assert issues.created == []
    assert issues.transitions == []


# ── create new-stage issue ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_stage_creates_unassigned_todo_issue(patch):
    stages = _FakeStagesRepo(
        current=None,  # project had no prior stage → nothing to close
        new_stage={"id": "20", "name": "Script"},
    )
    issues = _FakeIssueRepo()
    patch(
        stages=stages,
        issues=issues,
        project={"name": "My Film", "team_id": 42},
    )

    result = await advance_project_stage(100, 20, _USER)

    assert result == {"id": "20", "name": "Script"}
    assert len(issues.created) == 1
    payload = issues.created[0]
    assert payload["title"] == "My Film — Script"
    assert payload["status"] == "todo"
    assert payload["origin_kind"] == ORIGIN_KIND
    assert payload["origin_id"] == "project_stage:100:20"
    assert payload["project_id"] == 100
    assert payload["team_id"] == 42
    assert payload["created_by_user_id"] == _USER
    # Automation NEVER assigns — no silent指派/计费.
    assert "assignee_user_id" not in payload
    assert "assignee_agent_id" not in payload
    # No prior stage → no close.
    assert issues.transitions == []


@pytest.mark.asyncio
async def test_personal_project_omits_team_id(patch):
    stages = _FakeStagesRepo(current=None, new_stage={"id": "20", "name": "Script"})
    issues = _FakeIssueRepo()
    patch(stages=stages, issues=issues, project={"name": "Solo", "team_id": None})

    await advance_project_stage(100, 20, _USER)

    assert "team_id" not in issues.created[0]


# ── idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotent_reentry_skips_create(patch):
    new_origin = build_stage_origin_id(100, 20)
    stages = _FakeStagesRepo(current=None, new_stage={"id": "20", "name": "Script"})
    issues = _FakeIssueRepo(existing={new_origin: [{"id": 555, "status": "todo"}]})
    patch(stages=stages, issues=issues, project={"name": "P", "team_id": None})

    await advance_project_stage(100, 20, _USER)

    # Existing issue for this exact origin → no duplicate created.
    assert issues.created == []


# ── close old-stage issue ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closes_previous_stage_open_issue(patch):
    old_origin = build_stage_origin_id(100, 10)
    stages = _FakeStagesRepo(
        current={"id": "10", "name": "Planning"},
        new_stage={"id": "20", "name": "Script"},
    )
    issues = _FakeIssueRepo(
        existing={old_origin: [{"id": 111, "status": "in_progress"}]}
    )
    patch(stages=stages, issues=issues, project={"name": "P", "team_id": None})

    await advance_project_stage(100, 20, _USER)

    # Old-stage issue transitioned to done; new-stage issue created.
    assert issues.transitions == [(111, "done")]
    assert len(issues.created) == 1


@pytest.mark.asyncio
async def test_already_terminal_old_issue_not_reclosed(patch):
    old_origin = build_stage_origin_id(100, 10)
    stages = _FakeStagesRepo(
        current={"id": "10", "name": "Planning"},
        new_stage={"id": "20", "name": "Script"},
    )
    issues = _FakeIssueRepo(existing={old_origin: [{"id": 111, "status": "done"}]})
    patch(stages=stages, issues=issues, project={"name": "P", "team_id": None})

    await advance_project_stage(100, 20, _USER)

    assert issues.transitions == []


# ── best-effort: hook never blocks the advance ────────────────────────────────


@pytest.mark.asyncio
async def test_hook_failure_does_not_block_advance(patch, monkeypatch):
    stages = _FakeStagesRepo(current=None, new_stage={"id": "20", "name": "Script"})
    issues = _FakeIssueRepo(create_error=True)  # atomic_create raises
    patch(stages=stages, issues=issues, project={"name": "P", "team_id": None})

    # The advance still succeeds and returns the new stage.
    result = await advance_project_stage(100, 20, _USER)
    assert result == {"id": "20", "name": "Script"}
    assert issues.created == []  # create failed, swallowed


@pytest.mark.asyncio
async def test_project_fetch_failure_does_not_block_advance(patch, monkeypatch):
    stages = _FakeStagesRepo(current=None, new_stage={"id": "20", "name": "Script"})
    issues = _FakeIssueRepo()
    patch(stages=stages, issues=issues, project={"name": "P", "team_id": None})

    # Make the projects repo raise from inside the hook.
    class _Boom:
        async def get_project_by_id(self, pid):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: _Boom(),
    )

    result = await advance_project_stage(100, 20, _USER)
    assert result == {"id": "20", "name": "Script"}
    # Create never reached (project load blew up first), but advance is intact.
    assert issues.created == []


# ── advance delegates to the stage machine ────────────────────────────────────


@pytest.mark.asyncio
async def test_advance_delegates_to_set_current_stage(patch):
    stages = _FakeStagesRepo(current=None, new_stage={"id": "20", "name": "Script"})
    issues = _FakeIssueRepo()
    patch(stages=stages, issues=issues, project={"name": "P", "team_id": None})

    await advance_project_stage(100, 20, _USER)

    assert stages.set_calls == [(100, 20, _USER)]


def test_module_exposes_origin_kind():
    assert mod.ORIGIN_KIND == "project_stage"
