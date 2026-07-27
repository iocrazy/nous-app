"""DEPS_PENDING advance gate (mig 391, M3 PR-J task J2).

Two layers under test:
  1. ``advance_service._unmet_dependency_names`` — the pure predicate. A
     dependency is satisfied when the depended-on node is ``done`` OR
     ``skipped`` (spec §3); names are deduped and ordered by the unmet node's
     ``sort_order`` (never dict/iteration order); a depends_on id absent from
     the node map (deleted via FK CASCADE) is treated as already resolved.
  2. ``compute_advance_preview``/``execute_advance`` sharing this SAME
     predicate for the new ``BLOCK_DEPS_PENDING`` code — mirroring the #1400
     discipline already pinned for REVIEW_PENDING/DELIVERABLE_MISSING/
     FORM_INCOMPLETE. Unlike those three (which gate the CURRENT group's
     completion), DEPS_PENDING gates the TARGET (next) group's readiness to
     start — proven here by putting the unmet dependency on the group AFTER
     the active one. ``back`` (retreat) is proven never gated by deps.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.advance_service as advance_service
from app.schemas.workflow import BLOCK_DEPS_PENDING
from app.services.workflow.advance_service import _unmet_dependency_names


def _node_row(
    node_id: str,
    *,
    sort_order: int,
    parallel_group: Optional[int] = None,
    status: str = "pending",
    skipped: bool = False,
    depends_on: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": name or f"Node {node_id}",
        "sort_order": sort_order,
        "parallel_group": parallel_group,
        "review_required": False,
        "deliverable_required": False,
        "skipped": skipped,
        "status": status,
        "owner_user_id": None,
        "owner_agent_id": None,
        "planned_due": None,
        "folder_id": None,
        "form_schema": [],
        "form_data": {},
        "depends_on": depends_on or [],
    }


# ── _unmet_dependency_names: pure predicate ─────────────────────────────────


def test_no_deps_returns_empty():
    """Zero-impact for nodes carrying no depends_on at all (pre-mig-391 rows)."""
    target = [_node_row("2", sort_order=2)]
    node_by_id = {"1": _node_row("1", sort_order=1), "2": target[0]}
    assert _unmet_dependency_names(target, node_by_id) == []


def test_done_dependency_is_satisfied():
    dep = _node_row("1", sort_order=1, status="done")
    target_node = _node_row("2", sort_order=2, depends_on=["1"])
    node_by_id = {"1": dep, "2": target_node}
    assert _unmet_dependency_names([target_node], node_by_id) == []


def test_skipped_dependency_is_satisfied():
    """A skipped dependency counts as satisfied even though status stays
    non-done (spec §3: skipped is treated as done for gating purposes)."""
    dep = _node_row("1", sort_order=1, status="pending", skipped=True)
    target_node = _node_row("2", sort_order=2, depends_on=["1"])
    node_by_id = {"1": dep, "2": target_node}
    assert _unmet_dependency_names([target_node], node_by_id) == []


def test_pending_dependency_is_unmet():
    dep = _node_row("1", sort_order=1, status="in_progress")
    target_node = _node_row("2", sort_order=2, depends_on=["1"])
    node_by_id = {"1": dep, "2": target_node}
    assert _unmet_dependency_names([target_node], node_by_id) == ["Node 1"]


def test_partial_unmet_lists_only_unmet_names_in_sort_order():
    """Two deps, one done and one still pending, plus a third unrelated unmet
    dep from a different target node — only the actually-unmet ones are
    listed, ordered by the unmet node's sort_order (not target/dep insertion
    order)."""
    dep_done = _node_row("1", sort_order=1, status="done")
    dep_pending_a = _node_row("2", sort_order=3, status="pending", name="Dep C")
    dep_pending_b = _node_row("3", sort_order=2, status="pending", name="Dep B")
    target_1 = _node_row("4", sort_order=4, depends_on=["1", "2"])
    target_2 = _node_row("5", sort_order=4, depends_on=["3"])
    node_by_id = {
        "1": dep_done,
        "2": dep_pending_a,
        "3": dep_pending_b,
        "4": target_1,
        "5": target_2,
    }
    # sort_order: Dep B (2) < Dep C (3) → expect that order.
    assert _unmet_dependency_names([target_1, target_2], node_by_id) == [
        "Dep B",
        "Dep C",
    ]


def test_names_deduped_when_two_target_nodes_share_the_same_unmet_dep():
    dep_pending = _node_row("1", sort_order=1, status="pending")
    target_1 = _node_row("2", sort_order=2, depends_on=["1"])
    target_2 = _node_row("3", sort_order=2, depends_on=["1"])
    node_by_id = {"1": dep_pending, "2": target_1, "3": target_2}
    assert _unmet_dependency_names([target_1, target_2], node_by_id) == ["Node 1"]


def test_dependency_missing_from_node_map_treated_as_satisfied():
    """A depends_on id absent from node_by_id (the depended-on node was
    deleted — FK CASCADE already dropped the edge row server-side, but this
    defends the predicate itself) never blocks."""
    target_node = _node_row("2", sort_order=2, depends_on=["999"])
    node_by_id = {"2": target_node}
    assert _unmet_dependency_names([target_node], node_by_id) == []


# ── preview/execute integration — fake-repo pattern (mirrors
#    test_form_incomplete_predicate.py, trimmed to what the deps gate needs) ──


class _FakeNodesRepo:
    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = nodes
        self.current_node_id_calls: List[Any] = []

    async def list_nodes(self, project_id):
        return list(self._nodes)

    async def set_current_node_id(self, project_id, node_id):
        self.current_node_id_calls.append((project_id, node_id))

    async def list_folder_files(self, folder_id):
        return []


class _FakeProjectsRepo:
    def __init__(self, current_node_id: Optional[str]):
        self._current_node_id = current_node_id

    async def get_project_by_id(self, pid):
        return {"current_node_id": self._current_node_id}

    async def get_folders(self, project_id):
        return []

    async def get_project_files(self, project_id):
        return []


class _FakeIssueRepo:
    async def list_by_origin(self, kind, origin_id):
        return []

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        return {"id": issue_id, "status": new_status}

    async def list_children(self, issue_id):
        return []


def _install(
    monkeypatch,
    *,
    nodes_repo: _FakeNodesRepo,
    projects_repo: _FakeProjectsRepo,
    issue_repo: _FakeIssueRepo,
    role: Optional[str] = "manager",
):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.projects_repository.get_projects_repository",
        lambda: projects_repo,
    )
    monkeypatch.setattr(
        "app.repositories.issue_repository.get_issue_repository",
        lambda: issue_repo,
    )

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr(advance_service, "resolve_effective_role", _role)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_issues", _noop)
    monkeypatch.setattr(advance_service, "ensure_node_folders", _noop)
    monkeypatch.setattr(advance_service, "notify_stage_event", _noop)


@pytest.mark.asyncio
async def test_preview_blocks_forward_on_unmet_target_group_dependency(monkeypatch):
    """n1 is the active/current group; n2 (target group) depends on n3, a
    LATER node that is still pending — proving the gate looks at the TARGET
    group's deps, not the current group's."""
    n1 = _node_row("1", sort_order=1, status="in_progress")
    n2 = _node_row("2", sort_order=2, depends_on=["3"])
    n3 = _node_row("3", sort_order=3, status="pending")
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview("100", "user-1", "forward")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_DEPS_PENDING
    assert preview.waiting_on == ["Node 3"]


@pytest.mark.asyncio
async def test_preview_allows_forward_once_dependency_done(monkeypatch):
    n1 = _node_row("1", sort_order=1, status="in_progress")
    n2 = _node_row("2", sort_order=2, depends_on=["3"])
    n3 = _node_row("3", sort_order=3, status="done")
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview("100", "user-1", "forward")

    assert preview.will_advance is True
    assert preview.waiting_on == []


@pytest.mark.asyncio
async def test_preview_allows_forward_when_dependency_skipped(monkeypatch):
    """A skipped dependency satisfies the gate even though its status never
    reached ``done`` — skipped nodes are dropped from groups entirely, so
    they'd otherwise never clear a status check."""
    n1 = _node_row("1", sort_order=1, status="in_progress")
    n2 = _node_row("2", sort_order=2, depends_on=["3"])
    n3 = _node_row("3", sort_order=3, status="pending", skipped=True)
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview("100", "user-1", "forward")

    assert preview.will_advance is True
    assert preview.waiting_on == []


@pytest.mark.asyncio
async def test_execute_advance_blocked_by_deps_pending_does_not_mutate(monkeypatch):
    n1 = _node_row("1", sort_order=1, status="in_progress")
    n2 = _node_row("2", sort_order=2, depends_on=["3"])
    n3 = _node_row("3", sort_order=3, status="pending")
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    result = await advance_service.execute_advance("100", "user-1", "forward")

    assert result.will_advance is False
    assert result.blocked_reason == BLOCK_DEPS_PENDING
    assert nodes_repo.current_node_id_calls == []


@pytest.mark.asyncio
async def test_back_direction_is_never_gated_by_dependencies(monkeypatch):
    """Retreat must not consult depends_on at all (spec §3): the previous
    group's own unmet-looking deps (pointing forward, which would be invalid
    per the backward-only rule anyway) never block a back move."""
    n1 = _node_row("1", sort_order=1, status="pending")
    n2 = _node_row("2", sort_order=2, depends_on=["1"])
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("2")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview("100", "user-1", "back")

    assert preview.will_advance is True
    assert preview.creating[0].node_id == "1"


@pytest.mark.asyncio
async def test_no_deps_zero_regression_forward_still_advances(monkeypatch):
    """A project with no depends_on anywhere (pre-mig-391 shape) advances
    exactly as before — zero impact."""
    n1 = _node_row("1", sort_order=1)
    n2 = _node_row("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview("100", "user-1", "forward")

    assert preview.will_advance is True
    assert preview.waiting_on == []
    assert preview.creating[0].node_id == "2"
