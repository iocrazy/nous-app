"""``ensure_stage_issue`` — the idempotent per-node/stage mirror issue creator.

M2 PR-G/G1.5/fix-round retired the legacy SOP stage cursor end to end,
including ``advance_project_stage`` and ``sync_stage_issues`` (the
``set_current_stage`` post-commit callback that used to wrap
``ensure_stage_issue`` with open/close-old-issue logic). Both are gone — this
file used to drive ``ensure_stage_issue`` indirectly through
``advance_project_stage``; it now calls it directly, since that's exactly how
the live production caller (``ensure_node_issues``, used by
``instantiation.py`` / ``advance_service.py`` for workflow nodes) invokes it.

These are service-layer unit tests: the issue repo and projects repo are
faked; ``ensure_stage_issue`` itself never touches
``ProjectStagesRepository`` or the workflow-instance probe (those belonged
to the now-deleted SOP wrapper), so there is nothing stage-repo-shaped left
to fake here.
"""

from __future__ import annotations

import datetime

import pytest

import app.services.library.project_stage_issues as mod
from app.services.library.project_stage_issues import (
    ORIGIN_KIND,
    build_stage_origin_id,
    ensure_stage_issue,
)

_USER = "00000000-0000-0000-0000-000000000001"


# ── fakes ─────────────────────────────────────────────────────────────────────


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
    def _install(*, issues, project):
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


def test_module_exposes_origin_kind():
    assert mod.ORIGIN_KIND == "project_stage"


# ── create new-stage issue ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_stage_creates_unassigned_todo_issue(patch):
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "My Film", "team_id": 42})

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

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


@pytest.mark.asyncio
async def test_done_node_mirrors_as_done_issue(patch):
    """B4 fast-follow(终审 Minor#1): surface 自动完成可把未到达组的节点先推
    到 done;该组到达时镜像若仍按 todo 建,会留下「节点 done 但 Todolist 挂着
    open todo」的错位(末组/被闸挡时不会被 cascade 自愈)。done 节点的镜像
    直接以 done 落地——done→done 投影无害,状态两侧一致。"""
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "My Film", "team_id": 42})

    await ensure_stage_issue(
        issues, 100, {"id": "20", "name": "Script", "status": "done"}, _USER
    )

    assert issues.created[0]["status"] == "done"


@pytest.mark.asyncio
async def test_non_done_node_still_mirrors_as_todo(patch):
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "My Film", "team_id": 42})

    await ensure_stage_issue(
        issues, 100, {"id": "20", "name": "Script", "status": "in_progress"}, _USER
    )

    assert issues.created[0]["status"] == "todo"


@pytest.mark.asyncio
async def test_personal_project_omits_team_id(patch):
    # No owner_id on the project → the personal-team resolve short-circuits
    # before touching the repo, so the issue stays team-less.
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "Solo", "team_id": None})

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

    assert "team_id" not in issues.created[0]


class _FakeTeamRepo:
    def __init__(self, personal_id):
        self._personal = personal_id
        self.resolved_for = []

    async def get_personal_team_id(self, owner_id):
        self.resolved_for.append(owner_id)
        return self._personal


@pytest.mark.asyncio
async def test_personal_project_stamps_owner_personal_team(patch, monkeypatch):
    # team_id boundary translation: a NULL-team (personal) project resolves the
    # OWNER's personal team so the mirror issue is visible in the team-scoped
    # Todolist (the project row itself stays NULL).
    issues = _FakeIssueRepo()
    patch(
        issues=issues,
        project={"name": "Solo", "team_id": None, "owner_id": "owner-x"},
    )
    team_repo = _FakeTeamRepo("777")
    monkeypatch.setattr(
        "app.repositories.team_repository.get_team_repository",
        lambda: team_repo,
    )

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

    assert team_repo.resolved_for == ["owner-x"]
    # Snowflake resolved as a str → int-coerced onto the issue payload.
    assert issues.created[0]["team_id"] == 777


@pytest.mark.asyncio
async def test_personal_project_owner_without_personal_team_omits_team_id(
    patch, monkeypatch
):
    issues = _FakeIssueRepo()
    patch(
        issues=issues,
        project={"name": "Solo", "team_id": None, "owner_id": "owner-x"},
    )
    monkeypatch.setattr(
        "app.repositories.team_repository.get_team_repository",
        lambda: _FakeTeamRepo(None),  # owner has no personal team
    )

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

    assert "team_id" not in issues.created[0]


@pytest.mark.asyncio
async def test_team_project_keeps_explicit_team_without_resolving(patch, monkeypatch):
    # A project that already carries a team_id never triggers the personal-team
    # resolve (the fast path returns the explicit team).
    issues = _FakeIssueRepo()
    patch(
        issues=issues,
        project={"name": "Team Film", "team_id": 42, "owner_id": "owner-x"},
    )
    team_repo = _FakeTeamRepo("777")
    monkeypatch.setattr(
        "app.repositories.team_repository.get_team_repository",
        lambda: team_repo,
    )

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

    assert issues.created[0]["team_id"] == 42
    assert team_repo.resolved_for == []


# ── idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotent_reentry_skips_create(patch):
    new_origin = build_stage_origin_id(100, 20)
    issues = _FakeIssueRepo(existing={new_origin: [{"id": 555, "status": "todo"}]})
    patch(issues=issues, project={"name": "P", "team_id": None})

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

    # Existing issue for this exact origin → no duplicate created.
    assert issues.created == []


# ── M1 workflow node inheritance: owner + due_date, never dispatched ────────


@pytest.mark.asyncio
async def test_new_node_inherits_human_owner_as_assignee(patch):
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "P", "team_id": None})

    await ensure_stage_issue(
        issues,
        100,
        {
            "id": "20",
            "name": "Script",
            "owner_user_id": "00000000-0000-0000-0000-0000000000aa",
        },
        _USER,
    )

    payload = issues.created[0]
    assert payload["assignee_user_id"] == "00000000-0000-0000-0000-0000000000aa"
    assert "assignee_agent_id" not in payload


@pytest.mark.asyncio
async def test_new_node_inherits_agent_owner_as_assignee(patch):
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "P", "team_id": None})

    await ensure_stage_issue(
        issues,
        100,
        {
            "id": "20",
            "name": "Canvas",
            "owner_agent_id": "00000000-0000-0000-0000-0000000000bb",
        },
        _USER,
    )

    payload = issues.created[0]
    assert payload["assignee_agent_id"] == "00000000-0000-0000-0000-0000000000bb"
    assert "assignee_user_id" not in payload


@pytest.mark.asyncio
async def test_no_owner_leaves_issue_unassigned(patch):
    # Legacy SOP stage dicts (and un-owned nodes) carry neither owner key —
    # automation must never silently assign.
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "P", "team_id": None})

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

    payload = issues.created[0]
    assert "assignee_user_id" not in payload
    assert "assignee_agent_id" not in payload


@pytest.mark.asyncio
async def test_planned_due_inherited_as_real_date_object(patch):
    due = datetime.date(2026, 8, 1)
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "P", "team_id": None})

    await ensure_stage_issue(
        issues, 100, {"id": "20", "name": "Script", "planned_due": due}, _USER
    )

    payload = issues.created[0]
    assert payload["due_date"] is due
    assert isinstance(payload["due_date"], datetime.date)


@pytest.mark.asyncio
async def test_planned_due_as_iso_string_raises(patch):
    # The asyncpg DATE-bind footgun (CLAUDE.md known trap): an ISO string must
    # never reach the issue payload silently. Unlike the old sync_stage_issues
    # wrapper (deleted), ensure_stage_issue itself does NOT swallow this — the
    # caller (ensure_node_issues) is what wraps it best-effort.
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "P", "team_id": None})

    with pytest.raises(TypeError):
        await ensure_stage_issue(
            issues,
            100,
            {"id": "20", "name": "Script", "planned_due": "2026-08-01"},
            _USER,
        )

    assert issues.created == []


@pytest.mark.asyncio
async def test_no_due_date_omits_the_field(patch):
    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "P", "team_id": None})

    await ensure_stage_issue(issues, 100, {"id": "20", "name": "Script"}, _USER)

    assert "due_date" not in issues.created[0]


@pytest.mark.asyncio
async def test_agent_owner_arrival_never_dispatches(patch, monkeypatch):
    """An agent-owner node's mirror issue is ASSIGNED only — the run-confirm
    gate is untouched, so arrival must never reach the dispatch entry point."""
    import importlib

    # app/api/__init__.py rebinds the ``issues_router`` package attribute to
    # the router INSTANCE (``from ... import router as issues_router``), so a
    # plain ``import app.api.issues_router`` would resolve to the APIRouter,
    # not the module — importlib.import_module bypasses that shadowing.
    issues_router_mod = importlib.import_module("app.api.issues_router")

    def _boom(issue_id, wf_id):
        raise AssertionError("stage-node arrival must never dispatch an agent run")

    monkeypatch.setattr(issues_router_mod, "_dispatch_execute_issue", _boom)

    issues = _FakeIssueRepo()
    patch(issues=issues, project={"name": "P", "team_id": None})

    await ensure_stage_issue(
        issues,
        100,
        {
            "id": "20",
            "name": "Canvas",
            "owner_agent_id": "00000000-0000-0000-0000-0000000000bb",
        },
        _USER,
    )

    assert (
        issues.created[0]["assignee_agent_id"] == "00000000-0000-0000-0000-0000000000bb"
    )
