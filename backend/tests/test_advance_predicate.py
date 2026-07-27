"""Advance/retreat predicate (M1 PR-B B3).

``compute_advance_preview`` (pure read) and ``execute_advance`` share ONE
predicate — ``execute_advance`` literally calls ``compute_advance_preview``
first and refuses to mutate unless ``will_advance`` is True (#1400). These
tests exercise every blocked-reason branch plus the parallel-group and
retreat mechanics, with the repo layer faked (mirrors the fake-repo pattern
in ``test_project_stage_auto_issue.py``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

import app.services.workflow.advance_service as advance_service
from app.schemas.workflow import (
    BLOCK_DELIVERABLE_MISSING,
    BLOCK_NO_NEXT,
    BLOCK_NOT_MANAGER_OR_EDITOR,
    BLOCK_REVIEW_PENDING,
)

_USER = "00000000-0000-0000-0000-000000000001"
_PROJECT = "100"


def _node(
    node_id: str,
    *,
    sort_order: int,
    parallel_group: Optional[int] = None,
    review_required: bool = False,
    deliverable_required: bool = False,
    skipped: bool = False,
    owner_agent_id: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": f"Node {node_id}",
        "sort_order": sort_order,
        "parallel_group": parallel_group,
        "review_required": review_required,
        "deliverable_required": deliverable_required,
        "skipped": skipped,
        "owner_user_id": None,
        "owner_agent_id": owner_agent_id,
        "planned_due": None,
        "folder_id": folder_id,
    }


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeNodesRepo:
    def __init__(self, nodes: List[Dict[str, Any]]):
        self._nodes = nodes
        self.current_node_id_calls: List[Any] = []

    async def list_nodes(self, project_id):
        return list(self._nodes)

    async def set_current_node_id(self, project_id, node_id):
        self.current_node_id_calls.append((project_id, node_id))


class _FakeProjectsRepo:
    def __init__(
        self,
        current_node_id: Optional[str],
        *,
        folders: Optional[List[Dict[str, Any]]] = None,
        files: Optional[List[Dict[str, Any]]] = None,
    ):
        self._current_node_id = current_node_id
        self._folders = folders or []
        self._files = files or []

    async def get_project_by_id(self, pid):
        return {"current_node_id": self._current_node_id}

    async def get_folders(self, project_id):
        return self._folders

    async def get_project_files(self, project_id):
        return self._files


class _FakeIssueRepo:
    def __init__(
        self,
        *,
        by_node: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        children: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    ):
        self._by_node = by_node or {}
        self._children = children or {}
        self.transitions: List[Any] = []

    async def list_by_origin(self, kind, origin_id):
        # origin_id shape: project_stage:{project_id}:{node_id}
        node_id = origin_id.rsplit(":", 1)[-1]
        return list(self._by_node.get(node_id, []))

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {"id": issue_id, "status": new_status}

    async def list_children(self, issue_id):
        return self._children.get(issue_id, [])


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

    async def _ensure_node_issues(project_id, nodes, user_id):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_issues", _ensure_node_issues)

    async def _ensure_node_folders(project_id, nodes, user_id):
        return None

    monkeypatch.setattr(advance_service, "ensure_node_folders", _ensure_node_folders)

    # Stage notifications (E2) are a separate concern from the predicate/
    # mutation mechanics this file exercises — keep them a silent no-op here.
    # test_stage_notifications.py owns the notify_stage_event behavior itself.
    async def _notify_stage_event(**kwargs):
        return None

    monkeypatch.setattr(advance_service, "notify_stage_event", _notify_stage_event)


# ── NOT_MANAGER_OR_EDITOR ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_not_manager_or_editor_blocks(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node("1", sort_order=1)])
    projects_repo = _FakeProjectsRepo(None)
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        role="viewer",
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_NOT_MANAGER_OR_EDITOR


@pytest.mark.asyncio
async def test_no_role_at_all_blocks(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node("1", sort_order=1)])
    projects_repo = _FakeProjectsRepo(None)
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
        role=None,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.blocked_reason == BLOCK_NOT_MANAGER_OR_EDITOR


# ── REVIEW_PENDING ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_pending_blocks_forward(monkeypatch):
    n1 = _node("1", sort_order=1, review_required=True)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "in_review"}]})
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_REVIEW_PENDING


@pytest.mark.asyncio
async def test_review_satisfied_allows_forward(monkeypatch):
    n1 = _node("1", sort_order=1, review_required=True)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "done"}]})
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is True
    assert preview.creating[0].node_id == "2"


# ── DELIVERABLE_MISSING ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deliverable_missing_blocks_forward(monkeypatch):
    n1 = _node("1", sort_order=1, deliverable_required=True)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1", folders=[], files=[])
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_DELIVERABLE_MISSING


@pytest.mark.asyncio
async def test_deliverable_present_allows_forward(monkeypatch):
    n1 = _node("1", sort_order=1, deliverable_required=True)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo(
        "1", folders=[], files=[{"id": "f1", "folder_id": None}]
    )
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is True
    assert preview.creating[0].node_id == "2"


# ── DELIVERABLE via explicit folder_id (M2-W1) ──────────────────────────────


@pytest.mark.asyncio
async def test_deliverable_folder_id_with_file_allows_forward(monkeypatch):
    """folder_id set + a non-trashed file in THAT folder → deliverable present."""
    n1 = _node("1", sort_order=1, deliverable_required=True, folder_id="900")
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    # A file in a DIFFERENT folder must not count; the one in folder 900 does.
    projects_repo = _FakeProjectsRepo(
        "1",
        folders=[],
        files=[
            {"id": "fx", "folder_id": "111"},
            {"id": "f1", "folder_id": "900"},
        ],
    )
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is True
    assert preview.creating[0].node_id == "2"


@pytest.mark.asyncio
async def test_deliverable_folder_id_empty_folder_blocks_forward(monkeypatch):
    """folder_id set but no file lives in it → blocked, even if OTHER files exist."""
    n1 = _node("1", sort_order=1, deliverable_required=True, folder_id="900")
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo(
        "1", folders=[], files=[{"id": "fx", "folder_id": "111"}]
    )
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_DELIVERABLE_MISSING


@pytest.mark.asyncio
async def test_deliverable_fallback_name_match_folder(monkeypatch):
    """folder_id null → fallback: a file in a folder whose name == node name."""
    # _node names its node "Node 1"; a folder of that name holds the file.
    n1 = _node("1", sort_order=1, deliverable_required=True)  # folder_id None
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo(
        "1",
        folders=[{"id": "77", "name": "Node 1"}],
        files=[{"id": "f1", "folder_id": "77"}],
    )
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is True


# ── NO_NEXT ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_next_blocks_at_last_group(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node("1", sort_order=1)])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_NO_NEXT


@pytest.mark.asyncio
async def test_empty_workflow_blocks_no_next(monkeypatch):
    nodes_repo = _FakeNodesRepo([])
    projects_repo = _FakeProjectsRepo(None)
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.blocked_reason == BLOCK_NO_NEXT


# ── parallel group: all review-required members must clear ─────────────────


@pytest.mark.asyncio
async def test_parallel_group_requires_all_review_required_members_done(monkeypatch):
    n1 = _node("1", sort_order=1, parallel_group=1, review_required=True)
    n2 = _node("2", sort_order=2, parallel_group=1, review_required=True)
    n3 = _node("3", sort_order=3)
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(
        by_node={
            "1": [{"id": 501, "status": "done"}],
            "2": [{"id": 502, "status": "in_review"}],
        }
    )
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_REVIEW_PENDING


@pytest.mark.asyncio
async def test_parallel_group_advances_once_all_members_terminal(monkeypatch):
    n1 = _node("1", sort_order=1, parallel_group=1, review_required=True)
    n2 = _node("2", sort_order=2, parallel_group=1, review_required=True)
    n3 = _node("3", sort_order=3)
    nodes_repo = _FakeNodesRepo([n1, n2, n3])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(
        by_node={
            "1": [{"id": 501, "status": "done"}],
            "2": [{"id": 502, "status": "done"}],
        }
    )
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is True
    assert {n.node_id for n in preview.closing} == {"1", "2"}
    assert preview.creating[0].node_id == "3"


# ── warnings: agent-owner next group / open sub-issues ──────────────────────


@pytest.mark.asyncio
async def test_forward_warns_no_agent_auto_start(monkeypatch):
    n1 = _node("1", sort_order=1)
    n2 = _node("2", sort_order=2, owner_agent_id="agent-1")
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is True
    assert any("No agent will start automatically" in w for w in preview.warnings)


@pytest.mark.asyncio
async def test_forward_warns_about_open_subissues_but_still_advances(monkeypatch):
    n1 = _node("1", sort_order=1)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(
        by_node={"1": [{"id": 501, "status": "in_progress"}]},
        children={501: [{"id": 601, "status": "todo"}]},
    )
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "forward")

    assert preview.will_advance is True
    assert any("open sub-issue" in w for w in preview.warnings)


# ── retreat (back) ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_back_previews_reopen_of_previous_group(monkeypatch):
    n1 = _node("1", sort_order=1)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("2")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "back")

    assert preview.will_advance is True
    assert preview.creating[0].node_id == "1"
    assert preview.warnings  # explicit reopen warning per spec §5


@pytest.mark.asyncio
async def test_back_blocked_at_first_group(monkeypatch):
    nodes_repo = _FakeNodesRepo([_node("1", sort_order=1)])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    preview = await advance_service.compute_advance_preview(_PROJECT, _USER, "back")

    assert preview.will_advance is False
    assert preview.blocked_reason == BLOCK_NO_NEXT


# ── preview/execute share ONE predicate (#1400) ─────────────────────────────


@pytest.mark.asyncio
async def test_execute_calls_the_same_compute_advance_preview(monkeypatch):
    """``execute_advance`` must literally call ``compute_advance_preview`` — not
    a re-implemented copy of the rules — so the two can never drift."""
    n1 = _node("1", sort_order=1)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo()
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    calls: List[Any] = []
    original = advance_service.compute_advance_preview

    async def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return await original(*args, **kwargs)

    monkeypatch.setattr(advance_service, "compute_advance_preview", _spy)

    await advance_service.execute_advance(_PROJECT, _USER, "forward")

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_execute_blocked_preview_does_not_mutate(monkeypatch):
    n1 = _node("1", sort_order=1, review_required=True)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "in_review"}]})
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    result = await advance_service.execute_advance(_PROJECT, _USER, "forward")

    assert result.will_advance is False
    assert result.blocked_reason == BLOCK_REVIEW_PENDING
    assert nodes_repo.current_node_id_calls == []
    assert issue_repo.transitions == []


# ── execute: forward mutation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_forward_closes_current_group_and_moves_cursor(monkeypatch):
    n1 = _node("1", sort_order=1)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "in_progress"}]})
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    result = await advance_service.execute_advance(_PROJECT, _USER, "forward")

    assert result.will_advance is True
    assert issue_repo.transitions == [(501, "done")]
    assert nodes_repo.current_node_id_calls == [(_PROJECT, "2")]


@pytest.mark.asyncio
async def test_execute_forward_leaves_already_terminal_issue_untouched(monkeypatch):
    n1 = _node("1", sort_order=1)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("1")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "done"}]})
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    result = await advance_service.execute_advance(_PROJECT, _USER, "forward")

    assert result.will_advance is True
    assert issue_repo.transitions == []  # already done — no redundant transition


# ── execute: retreat mutation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_back_moves_cursor_and_reopens_issues(monkeypatch):
    n1 = _node("1", sort_order=1)
    n2 = _node("2", sort_order=2)
    nodes_repo = _FakeNodesRepo([n1, n2])
    projects_repo = _FakeProjectsRepo("2")
    issue_repo = _FakeIssueRepo(by_node={"1": [{"id": 501, "status": "done"}]})
    _install(
        monkeypatch,
        nodes_repo=nodes_repo,
        projects_repo=projects_repo,
        issue_repo=issue_repo,
    )

    result = await advance_service.execute_advance(_PROJECT, _USER, "back")

    assert result.will_advance is True
    assert nodes_repo.current_node_id_calls == [(_PROJECT, "1")]
    assert issue_repo.transitions == [(501, "in_progress")]
