"""Instance node add/delete service (M2-W3-1).

``add_project_node`` forwards a library (source_stage_id) or blank (name) add to
the repo; ``delete_project_node`` fences the removal behind three guards —
still-pending, no mirror issue, not in the active group — each surfacing a
machine reason the router maps to 409. Repo + issue layers are faked so every
branch runs without a database (mirrors the fake-repo pattern in
``test_advance_predicate.py``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

import app.repositories.issue_repository as issue_repo_mod
import app.repositories.project_stage_nodes_repository as nodes_repo_mod
import app.services.workflow.node_mutations as nm
from app.schemas.workflow import (
    DELETE_BLOCK_ACTIVE,
    DELETE_BLOCK_HAS_ISSUE,
    DELETE_BLOCK_NOT_PENDING,
    NodeDeleteBlocked,
)

_PROJECT = "100"
_NODE = "900"


class _FakeNodesRepo:
    def __init__(
        self,
        *,
        node: Optional[Dict[str, Any]],
        active: Optional[List[Dict[str, Any]]] = None,
        episode_scoped: bool = False,
    ):
        self._node = node
        self._active = active or []
        self._episode_scoped = episode_scoped
        self.add_calls: List[Dict[str, Any]] = []
        self.deleted: List[tuple] = []

    async def get_node(self, node_id, project_id=None):
        return self._node

    async def has_episode_scoped_nodes(self, project_id):
        # B3: add_project_node refuses to add a project-level node to a
        # per-episode project. Default False keeps these legacy add tests on the
        # project-level path; the refusal itself is covered by the integration
        # test test_add_node_refuses_per_episode_project.
        return self._episode_scoped

    async def get_active_group(self, project_id):
        return list(self._active)

    async def add_node(self, project_id, **kwargs):
        self.add_calls.append({"project_id": project_id, **kwargs})
        return {"id": "new-node", **kwargs}

    async def delete_node(self, node_id, project_id):
        self.deleted.append((str(node_id), str(project_id)))
        return True


class _FakeIssueRepo:
    def __init__(
        self, mirror: Optional[List[Dict[str, Any]]] = None, *, raise_on=False
    ):
        self._mirror = mirror or []
        self._raise = raise_on
        self.origins: List[tuple] = []

    async def list_by_origin(self, origin_kind, origin_id):
        self.origins.append((origin_kind, origin_id))
        if self._raise:
            raise RuntimeError("boom")
        return list(self._mirror)


def _install(monkeypatch, nodes_repo, issue_repo):
    monkeypatch.setattr(
        nodes_repo_mod, "get_project_stage_nodes_repository", lambda: nodes_repo
    )
    monkeypatch.setattr(issue_repo_mod, "get_issue_repository", lambda: issue_repo)


def _pending_node(**over: Any) -> Dict[str, Any]:
    return {"id": _NODE, "status": "pending", "name": "Voiceover", **over}


# ── add ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_from_library_forwards_source_stage_id(monkeypatch):
    repo = _FakeNodesRepo(node=None)
    _install(monkeypatch, repo, _FakeIssueRepo())
    await nm.add_project_node(_PROJECT, source_stage_id="55", sort_order=3)
    assert repo.add_calls == [
        {
            "project_id": _PROJECT,
            "source_stage_id": "55",
            "name": None,
            "sort_order": 3,
            "parallel_group": None,
        }
    ]


@pytest.mark.asyncio
async def test_add_blank_forwards_name(monkeypatch):
    repo = _FakeNodesRepo(node=None)
    _install(monkeypatch, repo, _FakeIssueRepo())
    await nm.add_project_node(_PROJECT, name="Extra QA", sort_order=1, parallel_group=2)
    call = repo.add_calls[0]
    assert call["name"] == "Extra QA"
    assert call["source_stage_id"] is None
    assert call["parallel_group"] == 2


# ── delete: happy path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_pending_no_issue_not_active_ok(monkeypatch):
    repo = _FakeNodesRepo(node=_pending_node(), active=[{"id": "other"}])
    issues = _FakeIssueRepo(mirror=[])
    _install(monkeypatch, repo, issues)
    assert await nm.delete_project_node(_PROJECT, _NODE) is True
    assert repo.deleted == [(_NODE, _PROJECT)]


# ── delete: guards ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_missing_node_raises_lookup(monkeypatch):
    repo = _FakeNodesRepo(node=None)
    _install(monkeypatch, repo, _FakeIssueRepo())
    with pytest.raises(LookupError):
        await nm.delete_project_node(_PROJECT, _NODE)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "in_review", "done", "skipped"])
async def test_delete_blocked_when_not_pending(monkeypatch, status):
    repo = _FakeNodesRepo(node=_pending_node(status=status))
    _install(monkeypatch, repo, _FakeIssueRepo())
    with pytest.raises(NodeDeleteBlocked) as exc:
        await nm.delete_project_node(_PROJECT, _NODE)
    assert exc.value.reason == DELETE_BLOCK_NOT_PENDING
    assert repo.deleted == []


@pytest.mark.asyncio
async def test_delete_blocked_when_in_active_group(monkeypatch):
    repo = _FakeNodesRepo(node=_pending_node(), active=[{"id": _NODE}])
    _install(monkeypatch, repo, _FakeIssueRepo())
    with pytest.raises(NodeDeleteBlocked) as exc:
        await nm.delete_project_node(_PROJECT, _NODE)
    assert exc.value.reason == DELETE_BLOCK_ACTIVE
    assert repo.deleted == []


@pytest.mark.asyncio
async def test_delete_blocked_when_mirror_issue_exists(monkeypatch):
    repo = _FakeNodesRepo(node=_pending_node(), active=[])
    issues = _FakeIssueRepo(mirror=[{"id": "1", "status": "in_progress"}])
    _install(monkeypatch, repo, issues)
    with pytest.raises(NodeDeleteBlocked) as exc:
        await nm.delete_project_node(_PROJECT, _NODE)
    assert exc.value.reason == DELETE_BLOCK_HAS_ISSUE
    assert repo.deleted == []
    # The origin lookup used the three-segment (project:node) origin id.
    assert issues.origins[0][1] == f"project_stage:{_PROJECT}:{_NODE}"


@pytest.mark.asyncio
async def test_delete_refuses_when_mirror_lookup_fails(monkeypatch):
    """Fail-safe: an unreadable issue store blocks the delete rather than
    letting a possibly-mirrored node be removed."""
    repo = _FakeNodesRepo(node=_pending_node(), active=[])
    issues = _FakeIssueRepo(raise_on=True)
    _install(monkeypatch, repo, issues)
    with pytest.raises(NodeDeleteBlocked) as exc:
        await nm.delete_project_node(_PROJECT, _NODE)
    assert exc.value.reason == DELETE_BLOCK_HAS_ISSUE
    assert repo.deleted == []
