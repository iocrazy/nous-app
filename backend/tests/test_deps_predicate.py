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

_EP1 = "8001"


def _node_row(
    node_id: str,
    *,
    sort_order: int,
    parallel_group: Optional[int] = None,
    status: str = "pending",
    skipped: bool = False,
    depends_on: Optional[List[str]] = None,
    name: Optional[str] = None,
    episode_id: str = _EP1,
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
        "episode_id": episode_id,
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


# ── exempt_ids: co-arrival + closing-current-group exemptions (M3 final
#    review #1/#2) ────────────────────────────────────────────────────────


def test_same_group_dependency_is_satisfied_when_target_group_is_exempt():
    """C depends_on B, both siblings in the SAME target group — B can never
    be 'done' before the group arrives (they arrive together), so this is
    satisfied ONLY when the call site passes the group's own ids as
    exempt_ids (the co-arrival half)."""
    node_b = _node_row("1", sort_order=1, status="pending")
    node_c = _node_row("2", sort_order=1, depends_on=["1"], status="pending")
    target = [node_b, node_c]
    node_by_id = {"1": node_b, "2": node_c}
    exempt_ids = {"1", "2"}
    assert _unmet_dependency_names(target, node_by_id, exempt_ids) == []


def test_closing_current_group_dependency_is_satisfied_when_exempt():
    """A dep target that is a member of the CLOSING current group (still
    in_progress, not yet 'done' at preview time) is satisfied ONLY when the
    call site includes that group's ids in exempt_ids (the closing-group
    half)."""
    closing_node = _node_row("1", sort_order=1, status="in_progress")
    target_node = _node_row("2", sort_order=2, depends_on=["1"])
    node_by_id = {"1": closing_node, "2": target_node}
    exempt_ids = {"1", "2"}
    assert _unmet_dependency_names([target_node], node_by_id, exempt_ids) == []


def test_same_group_dependency_without_exempt_ids_is_still_unmet():
    """The pure predicate does NOT auto-exempt same-group deps on its own —
    the exemption only applies when the call site explicitly builds
    exempt_ids from the relevant groups. Documents that the co-arrival/
    closing-group rulings live at the call site, not as an implicit
    assumption baked into this function."""
    node_b = _node_row("1", sort_order=1, status="pending")
    node_c = _node_row("2", sort_order=1, depends_on=["1"], status="pending")
    node_by_id = {"1": node_b, "2": node_c}
    assert _unmet_dependency_names([node_b, node_c], node_by_id) == ["Node 1"]


# ── preview/execute integration — fake-repo pattern (mirrors
#    test_form_incomplete_predicate.py, trimmed to what the deps gate needs) ──


class _FakeNodesRepo:
    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = nodes

    async def list_nodes_by_episode(self, project_id, episode_id):
        return [n for n in self._nodes if str(n.get("episode_id")) == str(episode_id)]

    async def list_folder_files(self, folder_id):
        return []


class _FakeEpisodesRepo:
    def __init__(self, cursors: Dict[str, Optional[str]]):
        self._cursors: Dict[str, Optional[str]] = {
            str(k): v for k, v in cursors.items()
        }
        self.set_current_node_id_calls: List[Any] = []

    async def get_by_id(self, episode_id):
        return {"current_node_id": self._cursors.get(str(episode_id))}

    async def set_current_node_id(self, episode_id, node_id):
        self.set_current_node_id_calls.append((str(episode_id), node_id))
        self._cursors[str(episode_id)] = node_id


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
    episodes_repo: Optional[_FakeEpisodesRepo] = None,
):
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: nodes_repo,
    )
    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: episodes_repo or _FakeEpisodesRepo({}),
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
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

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
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

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
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is True
    assert preview.waiting_on == []


@pytest.mark.asyncio
async def test_execute_advance_blocked_by_deps_pending_does_not_mutate(monkeypatch):
    n1 = _node_row("1", sort_order=1, status="in_progress")
    n2 = _node_row("2", sort_order=2, depends_on=["3"])
    n3 = _node_row("3", sort_order=3, status="pending")
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    result = await advance_service.execute_advance(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert result.will_advance is False
    assert result.blocked_reason == BLOCK_DEPS_PENDING
    assert episodes_repo.set_current_node_id_calls == []


@pytest.mark.asyncio
async def test_back_direction_is_never_gated_by_dependencies(monkeypatch):
    """Retreat must not consult depends_on at all (spec §3): the previous
    group's own unmet-looking deps (pointing forward, which would be invalid
    per the backward-only rule anyway) never block a back move."""
    n1 = _node_row("1", sort_order=1, status="pending")
    n2 = _node_row("2", sort_order=2, depends_on=["1"])
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("2")
    episodes_repo = _FakeEpisodesRepo({_EP1: "2"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview("100", "user-1", "back", episode_id=_EP1)

    assert preview.will_advance is True
    assert preview.creating[0].node_id == "1"


@pytest.mark.asyncio
async def test_preview_advances_when_next_group_dep_is_same_group_sibling(monkeypatch):
    """Regression for the Gate 5 same-group deadlock (M3 final review #1): C
    depends_on B, both B and C are parallel siblings in the group that would
    become next. Before the fix this deadlocked forever — B can't be 'done'
    before the group arrives (they arrive together), and the group can't
    arrive until B is done."""
    n1 = _node_row("1", sort_order=1, status="in_progress")  # active/closing
    n2 = _node_row("2", sort_order=2, parallel_group=1, status="pending")  # B
    n3 = _node_row(
        "3", sort_order=3, parallel_group=1, depends_on=["2"], status="pending"
    )  # C depends on B, same parallel_group as B
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is True
    assert preview.waiting_on == []


@pytest.mark.asyncio
async def test_preview_advances_when_next_group_depends_on_closing_current_group(
    monkeypatch,
):
    """Regression for the closing-current-group friction (M3 final review
    #2, controller ruling): the next group declares a dependency on the
    CURRENT (closing) group — the most natural template config — which must
    not DEPS_PENDING forever just because the current group's mirror issues
    haven't closed yet at preview time (they close during execute)."""
    n1 = _node_row("1", sort_order=1, status="in_progress")  # active/closing
    n2 = _node_row("2", sort_order=2, depends_on=["1"], status="pending")  # next
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is True
    assert preview.waiting_on == []


@pytest.mark.asyncio
async def test_preview_still_blocks_on_earlier_unrelated_unfinished_dependency(
    monkeypatch,
):
    """The exemption set is narrow (next_group ∪ closing current group
    only) — a dependency on a genuinely earlier, unrelated, unfinished node
    must still block, with the correct name in waiting_on."""
    n0 = _node_row("0", sort_order=1, status="pending", name="Earlier Node")
    n1 = _node_row("1", sort_order=2, status="in_progress")  # active/closing
    n2 = _node_row("2", sort_order=3, depends_on=["0"], status="pending")  # next
    nodes_repo = _FakeNodesRepo([n0, n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_DEPS_PENDING
    assert preview.waiting_on == ["Earlier Node"]


@pytest.mark.asyncio
async def test_no_deps_zero_regression_forward_still_advances(monkeypatch):
    """A project with no depends_on anywhere (pre-mig-391 shape) advances
    exactly as before — zero impact."""
    n1 = _node_row("1", sort_order=1)
    n2 = _node_row("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    episodes_repo = _FakeEpisodesRepo({_EP1: "1"})
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        episodes_repo=episodes_repo,
    )

    preview = await advance_service.compute_advance_preview(
        "100", "user-1", "forward", episode_id=_EP1
    )

    assert preview.will_advance is True
    assert preview.waiting_on == []
    assert preview.creating[0].node_id == "2"
