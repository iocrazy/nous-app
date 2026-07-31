"""POST /projects/{id}/workflow/nodes/{node_id}/start-early (M4 Autopilot,
task O2 brief) — the manual "先行开工" endpoint.

Goes through the REAL router function + the REAL ``node_start.start_node_now``
end to end (fakes only at the repo-getter seam, mirroring
``test_workflow_stage_board.py``'s pattern) so the shared helper autopilot's
own auto-start step reuses is itself exercised here, not just mocked.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

_USER = "00000000-0000-0000-0000-000000000001"
_AGENT = "00000000-0000-0000-0000-0000000000aa"
_MEMBER_USER = "00000000-0000-0000-0000-000000000cc"
_PROJECT = "100"
_NODE = "1"


class _Auth:
    def __init__(self, user_id: str):
        self.user_id = user_id


def _node(
    *,
    node_id: str = _NODE,
    status: str = "pending",
    skipped: bool = False,
    depends_on: Optional[List[str]] = None,
    owner_agent_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    members: Optional[List[Dict[str, Any]]] = None,
    sort_order: int = 2,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "project_id": _PROJECT,
        "name": "Stage",
        "sort_order": sort_order,
        "parallel_group": None,
        "status": status,
        "skipped": skipped,
        "owner_user_id": owner_user_id,
        "owner_agent_id": owner_agent_id,
        "members": members or [],
        "events": {"auto_start": False},
        "metadata": {},
        "depends_on": depends_on or [],
        "folder_id": None,
    }


class _FakeNodesRepo:
    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = {n["id"]: n for n in nodes}

    async def get_node(self, node_id, project_id=None):
        n = self._nodes.get(str(node_id))
        return dict(n) if n is not None else None

    async def list_nodes(self, project_id):
        return [dict(n) for n in self._nodes.values()]

    async def set_node_metadata(self, node_id, patch):
        n = self._nodes.get(str(node_id))
        if n is None:
            return None
        merged = dict(n.get("metadata") or {})
        merged.update(patch)
        n["metadata"] = merged
        return dict(merged)


class _FakeIssueRepo:
    def __init__(self):
        self._by_origin: Dict[str, List[Dict[str, Any]]] = {}
        self._next_id = 500
        self.transitions: List[Any] = []

    async def list_by_origin(self, kind, origin_id):
        return list(self._by_origin.get(origin_id, []))

    async def atomic_create(self, payload):
        self._next_id += 1
        row = {**payload, "id": self._next_id, "identifier": f"MH-{self._next_id}"}
        self._by_origin.setdefault(payload["origin_id"], []).append(row)
        return row

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        for issues in self._by_origin.values():
            for issue in issues:
                if issue["id"] == issue_id:
                    issue["status"] = new_status
        return {"id": issue_id, "status": new_status}


class _FakeProjectsRepo:
    async def get_project_by_id(self, pid):
        return {"name": "Proj", "owner_id": _USER, "team_id": 777}


class _NotifySpy:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, user_id, kind, title, **kwargs):
        self.calls.append({"user_id": user_id, "kind": kind, "title": title})
        return 1


def _install(
    monkeypatch,
    *,
    nodes_repo: _FakeNodesRepo,
    issue_repo: Optional[_FakeIssueRepo] = None,
    role: Optional[str] = "manager",
    notify_spy: Optional[_NotifySpy] = None,
):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    if issue_repo is not None:
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issue_repo,
        )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: _FakeProjectsRepo(),
    )

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr("app.core.workflow_roles.resolve_effective_role", _role)

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr("app.services.workflow.node_folders.ensure_node_folders", _noop)

    if notify_spy is not None:
        # stage_hook.py's _notify_prepared does a per-call LOCAL import (so
        # patching the source module is enough), but
        # stage_notifications.notify_stage_event imports `notify` at MODULE
        # LEVEL (top of file) — its own already-bound name must be patched
        # too, or the arrival notify still hits the real notify().
        monkeypatch.setattr("app.services.notifications.notify", notify_spy)
        monkeypatch.setattr(
            "app.services.workflow.stage_notifications.notify", notify_spy
        )


def _router():
    # app/api/__init__.py shadows the submodule attribute with the bare
    # APIRouter instance — importlib gets the real module (established
    # pattern, see test_workflow_stage_board.py's module docstring).
    return importlib.import_module("app.api.projects_router")


async def _call(project_id, node_id, user_id):
    return await _router().start_workflow_node_early(
        project_id, node_id, _Auth(user_id), None
    )


# ── 404 before 403 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_not_found_404_before_role_check(monkeypatch):
    nodes_repo = _FakeNodesRepo([])  # no node with id _NODE

    def _boom(*a, **kw):
        raise AssertionError("role must not be resolved before the 404 check")

    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr("app.core.workflow_roles.resolve_effective_role", _boom)

    with pytest.raises(HTTPException) as exc:
        await _call(_PROJECT, _NODE, _USER)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_insufficient_role_403(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node()])
    _install(monkeypatch, nodes_repo=nodes_repo, role="viewer")

    with pytest.raises(HTTPException) as exc:
        await _call(_PROJECT, _NODE, _USER)

    assert exc.value.status_code == 403


# ── 422 business-rule gates ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_already_started_node_422(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node(status="in_progress")])
    _install(monkeypatch, nodes_repo=nodes_repo)

    with pytest.raises(HTTPException) as exc:
        await _call(_PROJECT, _NODE, _USER)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "NODE_NOT_PENDING"


@pytest.mark.asyncio
async def test_skipped_node_422(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node(skipped=True)])
    _install(monkeypatch, nodes_repo=nodes_repo)

    with pytest.raises(HTTPException) as exc:
        await _call(_PROJECT, _NODE, _USER)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "NODE_NOT_PENDING"


@pytest.mark.asyncio
async def test_deps_pending_422_with_waiting_on(monkeypatch):
    blocker = _node(node_id="2", status="pending", sort_order=1)
    candidate = _node(node_id="1", depends_on=["2"], sort_order=2)
    candidate["name"] = "Editing"
    blocker["name"] = "Script"
    nodes_repo = _FakeNodesRepo([blocker, candidate])
    _install(monkeypatch, nodes_repo=nodes_repo)

    with pytest.raises(HTTPException) as exc:
        await _call(_PROJECT, "1", _USER)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "DEPS_PENDING"
    assert exc.value.detail["waiting_on"] == ["Script"]


# ── success paths ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_starts_node_no_agent_owner(monkeypatch):
    # Owner distinct from the acting user — notify_stage_event excludes the
    # actor from recipients, so the actor being the owner would leave an
    # empty (untestable) recipient set.
    nodes_repo = _FakeNodesRepo([_node(owner_user_id=_MEMBER_USER)])
    issue_repo = _FakeIssueRepo()
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    result = await _call(_PROJECT, _NODE, _USER)

    assert result["success"] is True
    assert issue_repo.transitions and issue_repo.transitions[0][1] == "in_progress"
    # Arrival notify fired (notify_stage_event's default notify_on_arrival=True).
    assert notify_spy.calls


@pytest.mark.asyncio
async def test_agent_owner_never_dispatches_goes_through_confirm_gate(monkeypatch):
    node = _node(owner_agent_id=_AGENT, members=[{"user_id": _MEMBER_USER}])
    nodes_repo = _FakeNodesRepo([node])
    issue_repo = _FakeIssueRepo()
    notify_spy = _NotifySpy()
    _install(
        monkeypatch, nodes_repo=nodes_repo, issue_repo=issue_repo, notify_spy=notify_spy
    )

    def _boom(issue_id, wf_id, *, auto=False):
        raise AssertionError("start-early must NEVER dispatch an agent run")

    # app/api/__init__.py shadows the `issues_router` submodule attribute on
    # the `app.api` package with the bare APIRouter instance once imported,
    # so the string-path form of setattr would resolve to the wrong object
    # (see test_stage_hook.py's identical workaround) — patch the real
    # module object instead.
    issues_router_mod = importlib.import_module("app.api.issues_router")
    monkeypatch.setattr(issues_router_mod, "_dispatch_execute_issue", _boom)

    result = await _call(_PROJECT, _NODE, _USER)

    assert result["success"] is True
    # The issue still started (todo -> in_progress) ...
    assert issue_repo.transitions and issue_repo.transitions[0][1] == "in_progress"
    # ... but the agent run only reached the M3 confirm-gate "upshot" —
    # a run-ready notification, never a dispatch (the _boom spy above proves
    # that structurally; this proves the positive behavior happened too).
    titles = [c["title"] for c in notify_spy.calls]
    assert any("ready" in t.lower() for t in titles)
