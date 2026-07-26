"""Owner-review guard on project-stage issue transitions (M1 PR-B).

A ``project_stage`` mirror issue moving in_review->done may only be closed by
the node's own owner, or the project manager as an override. Handlers are
called directly (as in test_workflows_router.py) with the module-level
``issue_repository`` singleton and the node-repo/role-resolver seams
monkeypatched — no HTTP layer, no DB.
"""

from __future__ import annotations

import datetime
import importlib
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from app.schemas.issue import IssueStatus, IssueStatusTransition

router_mod = importlib.import_module("app.api.issues_router")

_NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
_OWNER = "00000000-0000-0000-0000-000000000001"
_OTHER = "00000000-0000-0000-0000-000000000002"


class _Auth:
    def __init__(self, user_id: str):
        self.user_id = user_id


def _issue_row(**overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": 900,
        "issue_number": 1,
        "identifier": "MH-900",
        "title": "Script",
        "description": None,
        "status": "in_review",
        "priority": "medium",
        "team_id": None,
        "project_id": 100,
        "parent_id": None,
        "assignee_user_id": None,
        "assignee_agent_id": None,
        "origin_kind": "project_stage",
        "origin_id": "project_stage:100:1",
        "origin_fingerprint": "default",
        "billing_code": None,
        "created_by_user_id": _OTHER,
        "created_by_agent_id": None,
        "dbos_workflow_id": None,
        "execution_locked_at": None,
        "execution_state": None,
        "request_depth": 0,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "hidden_at": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    row.update(overrides)
    return row


class _FakeIssueRepo:
    def __init__(self, issue: Dict[str, Any]):
        self._issue = issue
        self.transitions = []

    async def get_by_id(self, issue_id):
        return dict(self._issue)

    async def is_team_member(self, user_id, team_id):
        return True

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {**self._issue, "status": new_status}


class _FakeNodesRepo:
    def __init__(self, node: Optional[Dict[str, Any]]):
        self._node = node

    async def get_node(self, node_id, project_id=None):
        return self._node


class _ExplodingNodesRepo:
    """Proves the guard never even reaches the node repo for scopes it must
    not lock (non-project_stage issues, old two-segment origins)."""

    async def get_node(self, *args, **kwargs):
        raise AssertionError("guard must not touch the node repo here")


def _install(monkeypatch, *, issue_repo, nodes_repo=None, role="editor"):
    monkeypatch.setattr(router_mod, "issue_repository", issue_repo)
    if nodes_repo is not None:
        monkeypatch.setattr(
            "app.repositories.project_stage_nodes_repository."
            "get_project_stage_nodes_repository",
            lambda: nodes_repo,
        )

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr("app.core.workflow_roles.resolve_effective_role", _role)


# ── owner passes / non-owner blocked / manager override ─────────────────────


@pytest.mark.asyncio
async def test_owner_can_complete_review(monkeypatch):
    auth = _Auth(_OWNER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OWNER))
    nodes_repo = _FakeNodesRepo({"owner_user_id": _OWNER, "owner_agent_id": None})
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="editor")

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE
    assert issue_repo.transitions == [(900, "done")]


@pytest.mark.asyncio
async def test_non_owner_editor_403(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo({"owner_user_id": _OWNER, "owner_agent_id": None})
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="editor")

    with pytest.raises(HTTPException) as exc:
        await router_mod.transition_status(
            900, IssueStatusTransition(status=IssueStatus.DONE), auth
        )

    assert exc.value.status_code == 403
    assert issue_repo.transitions == []


@pytest.mark.asyncio
async def test_manager_override_allows(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo({"owner_user_id": _OWNER, "owner_agent_id": None})
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="manager")

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE
    assert issue_repo.transitions == [(900, "done")]


# ── agent-owner nodes have no human owner — manager only ────────────────────


@pytest.mark.asyncio
async def test_agent_owner_node_blocks_non_manager(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo({"owner_user_id": None, "owner_agent_id": "agent-1"})
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="editor")

    with pytest.raises(HTTPException) as exc:
        await router_mod.transition_status(
            900, IssueStatusTransition(status=IssueStatus.DONE), auth
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_agent_owner_node_allows_manager(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo({"owner_user_id": None, "owner_agent_id": "agent-1"})
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="manager")

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE


# ── scope: only project_stage in_review->done, only the new origin shape ───


@pytest.mark.asyncio
async def test_non_project_stage_issue_not_locked(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(
        _issue_row(created_by_user_id=_OTHER, origin_kind="manual", origin_id=None)
    )
    _install(
        monkeypatch,
        issue_repo=issue_repo,
        nodes_repo=_ExplodingNodesRepo(),
        role="editor",
    )

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE


@pytest.mark.asyncio
async def test_old_two_segment_origin_not_locked(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(
        _issue_row(created_by_user_id=_OTHER, origin_id="project_stage:55")
    )
    _install(
        monkeypatch,
        issue_repo=issue_repo,
        nodes_repo=_ExplodingNodesRepo(),
        role="editor",
    )

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE


@pytest.mark.asyncio
async def test_guard_only_fires_on_in_review_to_done_edge(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(
        _issue_row(created_by_user_id=_OTHER, status="in_progress")
    )
    _install(
        monkeypatch,
        issue_repo=issue_repo,
        nodes_repo=_ExplodingNodesRepo(),
        role="editor",
    )

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.IN_REVIEW), auth
    )

    assert result.status == IssueStatus.IN_REVIEW


@pytest.mark.asyncio
async def test_no_project_id_not_locked(monkeypatch):
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER, project_id=None))
    _install(
        monkeypatch,
        issue_repo=issue_repo,
        nodes_repo=_ExplodingNodesRepo(),
        role="editor",
    )

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE


# ── completion_policy='any_editor' (mig 386, M2 PR-D) ───────────────────────


@pytest.mark.asyncio
async def test_any_editor_policy_allows_non_owner_editor(monkeypatch):
    """A node with completion_policy='any_editor' lets any editor (not just
    the owner or a manager override) close the review."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo(
        {
            "owner_user_id": _OWNER,
            "owner_agent_id": None,
            "completion_policy": "any_editor",
        }
    )
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="editor")

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE
    assert issue_repo.transitions == [(900, "done")]


@pytest.mark.asyncio
async def test_any_editor_policy_still_blocks_viewer(monkeypatch):
    """'any_editor' widens the gate to manager/editor — not to every role."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo(
        {
            "owner_user_id": _OWNER,
            "owner_agent_id": None,
            "completion_policy": "any_editor",
        }
    )
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="viewer")

    with pytest.raises(HTTPException) as exc:
        await router_mod.transition_status(
            900, IssueStatusTransition(status=IssueStatus.DONE), auth
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_policy_default_unaffected_by_any_editor_change(monkeypatch):
    """Regression guard: a node with completion_policy='owner' (or the key
    missing entirely, as legacy fixtures assume) keeps rejecting a non-owner
    editor exactly as before this change."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo(
        {"owner_user_id": _OWNER, "owner_agent_id": None, "completion_policy": "owner"}
    )
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="editor")

    with pytest.raises(HTTPException) as exc:
        await router_mod.transition_status(
            900, IssueStatusTransition(status=IssueStatus.DONE), auth
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_node_lookup_miss_fails_open(monkeypatch):
    """The node id resolves (new-format origin) but no live row is found —
    there's no owner to enforce against, so the transition proceeds."""
    auth = _Auth(_OTHER)
    issue_repo = _FakeIssueRepo(_issue_row(created_by_user_id=_OTHER))
    nodes_repo = _FakeNodesRepo(None)
    _install(monkeypatch, issue_repo=issue_repo, nodes_repo=nodes_repo, role="editor")

    result = await router_mod.transition_status(
        900, IssueStatusTransition(status=IssueStatus.DONE), auth
    )

    assert result.status == IssueStatus.DONE
