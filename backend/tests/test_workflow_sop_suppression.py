"""W2-1: a workflow-instance project's legacy SOP mirror is suppressed.

A project running a workflow instance mirrors its node chain
(``ensure_node_issues``); the global-SOP mirror must stand down so带 workflow
的 project 不再产生两套镜像 issue. These service-layer unit tests fake the
issue repo and monkeypatch the ``project_has_workflow_nodes`` probe to drive
both branches without a database.
"""

from __future__ import annotations

import pytest

from app.services.library.project_stage_issues import (
    advance_project_stage,
    build_stage_origin_id,
)

_USER = "00000000-0000-0000-0000-000000000001"


# ── fakes (mirror test_project_stage_auto_issue's contract) ───────────────────


class _FakeStagesRepo:
    def __init__(self, current, new_stage):
        self._current = current
        self._new_stage = new_stage
        self.set_calls = []

    async def get_current(self, pid):
        return self._current

    async def set_current_stage(self, pid, stage_id, user_id):
        self.set_calls.append((pid, stage_id, user_id))
        if self._new_stage is None:
            return None
        from app.services.library.project_stage_issues import sync_stage_issues

        old_id = (self._current or {}).get("id")
        await sync_stage_issues(int(pid), old_id, self._new_stage, user_id)
        return self._new_stage


class _FakeIssueRepo:
    def __init__(self, existing=None):
        self._existing = existing or {}
        self.created = []
        self.transitions = []

    async def list_by_origin(self, origin_kind, origin_id, *, include_hidden=False):
        return list(self._existing.get(origin_id, []))

    async def atomic_create(self, payload):
        self.created.append(payload)
        return {**payload, "id": 999}

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
    def _install(*, stages, issues, project, has_workflow):
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

        async def _probe(project_id):
            return has_workflow

        # Patch at the definition module AND the import site inside
        # project_stage_issues (it does a function-level import).
        monkeypatch.setattr(
            "app.services.workflow.instantiation.project_has_workflow_nodes",
            _probe,
        )

    return _install


# ── workflow project: SOP mirror suppressed ───────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_project_skips_sop_mirror(patch):
    """A project with a workflow instance derives NO SOP mirror issue — the node
    chain is the only mirror source."""
    stages = _FakeStagesRepo(
        current={"id": "10", "name": "Planning"},
        new_stage={"id": "20", "name": "Script"},
    )
    issues = _FakeIssueRepo(
        existing={build_stage_origin_id(100, 10): [{"id": 111, "status": "todo"}]}
    )
    patch(
        stages=stages,
        issues=issues,
        project={"name": "WF", "team_id": 42},
        has_workflow=True,
    )

    result = await advance_project_stage(100, 20, _USER)

    # The stage transition itself still happened…
    assert result == {"id": "20", "name": "Script"}
    assert stages.set_calls == [(100, 20, _USER)]
    # …but the SOP mirror stood down entirely: no create, no close.
    assert issues.created == []
    assert issues.transitions == []


# ── no-workflow project: mirror proceeds (regression) ─────────────────────────


@pytest.mark.asyncio
async def test_non_workflow_project_still_mirrors(patch):
    """The suppression is keyed strictly to workflow projects — a plain SOP
    project keeps its mirror (create new + close old) exactly as before."""
    stages = _FakeStagesRepo(
        current={"id": "10", "name": "Planning"},
        new_stage={"id": "20", "name": "Script"},
    )
    issues = _FakeIssueRepo(
        existing={build_stage_origin_id(100, 10): [{"id": 111, "status": "todo"}]}
    )
    patch(
        stages=stages,
        issues=issues,
        project={"name": "SOP", "team_id": 42},
        has_workflow=False,
    )

    await advance_project_stage(100, 20, _USER)

    # New-stage issue created, old-stage issue closed — legacy behaviour intact.
    assert len(issues.created) == 1
    assert issues.created[0]["origin_id"] == build_stage_origin_id(100, 20)
    assert issues.transitions == [(111, "done")]
